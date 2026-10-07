#!/usr/bin/env python3
#d: Install and manage dusky commands

import argparse
import fcntl
import hashlib
import json
import os
import pwd
import re
import shutil
import signal
import subprocess
import sys
import threading
import time
import traceback
from dataclasses import dataclass
from enum import Enum
from functools import cache
from pathlib import Path
from typing import Any, NamedTuple


def _json_excepthook(exc_type: type, exc_value: BaseException, exc_traceback: Any) -> None:
    """Enforces strict JSON output to STDERR to prevent stdout pipeline corruption."""
    if issubclass(exc_type, (KeyboardInterrupt, SystemExit)):
        sys.__excepthook__(exc_type, exc_value, exc_traceback)
        return
    err_msg = "".join(traceback.format_exception(exc_type, exc_value, exc_traceback))
    print(json.dumps({"status": "fatal", "error": str(exc_value), "traceback": err_msg}), file=sys.stderr)


# --- Rich UI Presentation Guard ---
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
    Console = Panel = Confirm = Table = Text = None
    console = error_console = None


def check_ui_deps(is_json: bool) -> None:
    """Enforces Rich UI requirement only if not running in JSON mode."""
    if not RICH_AVAILABLE and not is_json:
        print("ERROR: The 'rich' python library is required for interactive/UI mode.", file=sys.stderr)
        print("Install it via: sudo pacman -S python-rich", file=sys.stderr)
        sys.exit(1)


# ==============================================================================
# 1. USER CONFIGURATION AREA — DEFINE STAGE FLEET COMMANDS HERE
# ==============================================================================

class Mode(Enum):
    USER = "U"   # Runs as normal unprivileged user
    SUDO = "S"   # Runs as root via sudo


@dataclass(frozen=True)
class FleetCommand:
    mode: Mode
    cmd: str
    description: str = ""
    id_salt: str = ""  # Optional salt for explicit disambiguation if needed

    @property
    def state_hash(self) -> str:
        # Maintain 100% SHA256 string parity with original bash scripts: sha256("MODE | COMMAND")
        if self.id_salt:
            raw_entry = f"{self.mode.value} | {self.id_salt} | {self.cmd}"
        else:
            raw_entry = f"{self.mode.value} | {self.cmd}"
        return hashlib.sha256(raw_entry.encode("utf-8")).hexdigest()


# ------------------------------------------------------------------------------
# Stage 1: Setup / Post-Install Stage (legacy 480_dusky_commands.sh)
# ------------------------------------------------------------------------------
SETUP_COMMANDS: list[FleetCommand] = [
    FleetCommand(Mode.USER, 'gsettings set org.gnome.desktop.interface icon-theme "Papirus"', "Set GNOME Icon Theme"),
    FleetCommand(Mode.USER, 'gsettings set org.gnome.desktop.interface gtk-theme "adw-gtk3"', "Set GNOME GTK Theme"),
    # FleetCommand(Mode.USER, 'gsettings set org.cinnamon.desktop.default-applications.terminal exec "kitty"', "Set Cinnamon Default Terminal"),
    FleetCommand(Mode.USER, 'mkdir -p "$HOME/.config/gtk-3.0" "$HOME/.config/gtk-4.0"', "Create GTK Config Directories"),
    FleetCommand(Mode.USER, 'ln -nfs "$HOME/.config/matugen/generated/gtk-3.css" "$HOME/.config/gtk-3.0/gtk.css"', "Link Matugen GTK-3 CSS"),
    FleetCommand(Mode.USER, 'ln -nfs "$HOME/.config/matugen/generated/gtk-4.css" "$HOME/.config/gtk-4.0/gtk.css"', "Link Matugen GTK-4 CSS"),
    FleetCommand(Mode.USER, 'ln -nfs "/usr/share/themes/adw-gtk3/gtk-4.0/libadwaita.css" "$HOME/.config/gtk-4.0/libadwaita.css"', "Link Adw-GTK3 Libadwaita CSS"),
    FleetCommand(Mode.USER, 'ln -nfs "/usr/share/themes/adw-gtk3/gtk-4.0/libadwaita-tweaks.css" "$HOME/.config/gtk-4.0/libadwaita-tweaks.css"', "Link Libadwaita Tweaks CSS"),
    FleetCommand(Mode.USER, 'mkdir -p "$HOME/Documents/dusky_backups/"', "Create Backups Directory"),
    FleetCommand(
        Mode.USER,
        'TARGET="$HOME/user_scripts/dusky_system/click_away_to_dismiss" && '
        'wayland-scanner client-header "$TARGET/hyprland-focus-grab-v1.xml" "$TARGET/hyprland-focus-grab-v1-client-protocol.h" && '
        'wayland-scanner private-code "$TARGET/hyprland-focus-grab-v1.xml" "$TARGET/hyprland-focus-grab-v1-client-protocol.c" && '
        'gcc -shared -fPIC -o "$TARGET/libwaylandgrab.so" "$TARGET/dusky.c" "$TARGET/hyprland-focus-grab-v1-client-protocol.c" -lwayland-client -lpthread -ldl',
        "Compile Wayland Click-Away Shared Library"
    ),
    FleetCommand(Mode.USER, '"$HOME/user_scripts/dusky_system/reload_cc/cc_restart.sh" --quiet >/dev/null 2>&1 < /dev/null &', "Background Restart Control Center"),
    FleetCommand(Mode.USER, 'update-desktop-database "$HOME/.local/share/applications" || true', "Update Desktop Entry Database Cache"),
    # --- System Services ---
    # FleetCommand(Mode.USER, 'systemctl --user disable dusky.service || true', "Disable Legacy Dusky Service"),
    # FleetCommand(Mode.SUDO, 'systemctl enable --now tlp.service || true', "Enable TLP Power Management Service"),
]

# ------------------------------------------------------------------------------
# Stage 2: Pre-System-Update Stage (legacy dusky_commands_before.sh)
# ------------------------------------------------------------------------------
BEFORE_COMMANDS: list[FleetCommand] = [
    # --- UI & Theming ---
    # FleetCommand(Mode.USER, "gsettings set org.gnome.desktop.interface icon-theme 'Papirus'", "Set GNOME Icon Theme"),
    # for nemo right click
    # FleetCommand(Mode.USER, "gsettings set org.cinnamon.desktop.default-applications.terminal exec 'kitty'", "Set Cinnamon Default Terminal"),
    FleetCommand(Mode.USER, 'mkdir -p ~/.config/opencode/themes || true', "Create Opencode Themes Directory"),
    FleetCommand(Mode.USER, 'mkdir -p ~/.config/Kvantum/matugen || true', "Create Kvantum Matugen Directory"),
    FleetCommand(Mode.USER, 'systemctl --user disable --now dusky_sliders.service || true', "Disable Legacy Sliders Service"),
    # --- Remove old dusky_snapshot units before re-deployment ---
    FleetCommand(
        Mode.SUDO,
        'systemctl stop dusky_snapshot.timer dusky_snapshot.service 2>/dev/null || true; '
        'systemctl disable dusky_snapshot.timer 2>/dev/null || true',
        "Stop & Disable Snapshot Service"
    ),
    FleetCommand(Mode.SUDO, 'rm -f /etc/systemd/system/dusky_snapshot.service /etc/systemd/system/dusky_snapshot.timer', "Remove Snapshot Unit Files"),
    FleetCommand(Mode.SUDO, 'systemctl daemon-reload', "Systemd System Daemon Reload"),
    # --- System Services ---
    # FleetCommand(Mode.USER, 'systemctl --user disable dusky.service || true', "Disable Legacy Dusky Service"),
    # FleetCommand(Mode.SUDO, 'systemctl enable --now tlp.service || true', "Enable TLP Power Management Service"),
]

# ------------------------------------------------------------------------------
# Stage 3: Post-System-Update Stage (legacy dusky_commands_after.sh)
# ------------------------------------------------------------------------------
AFTER_COMMANDS: list[FleetCommand] = [
    # --- UI & Theming ---
    # FleetCommand(Mode.USER, "gsettings set org.gnome.desktop.interface icon-theme 'Papirus'", "Set GNOME Icon Theme"),
    # FleetCommand(Mode.USER, "gsettings set org.cinnamon.desktop.default-applications.terminal exec 'kitty'", "Set Cinnamon Default Terminal"),
    # --- System Services ---
    # FleetCommand(Mode.USER, 'systemctl --user disable dusky.service || true', "Disable Legacy Dusky Service"),
    # FleetCommand(Mode.SUDO, 'systemctl enable --now tlp.service || true', "Enable TLP Power Management Service"),
    FleetCommand(Mode.USER, 'hyprctl reload', "Reload Hyprland Configuration"),
    FleetCommand(Mode.USER, 'systemctl --user enable --now mako.service || true', "Enable & Start Mako Notification Daemon"),
    FleetCommand(Mode.SUDO, 'systemctl enable --now ufw.service || true', "Enable & Start UFW Firewall Service"),
    FleetCommand(
        Mode.USER,
        'TARGET="$HOME/user_scripts/dusky_system/click_away_to_dismiss" && '
        'wayland-scanner client-header "$TARGET/hyprland-focus-grab-v1.xml" "$TARGET/hyprland-focus-grab-v1-client-protocol.h" && '
        'wayland-scanner private-code "$TARGET/hyprland-focus-grab-v1.xml" "$TARGET/hyprland-focus-grab-v1-client-protocol.c" && '
        'gcc -shared -fPIC -o "$TARGET/libwaylandgrab.so" "$TARGET/dusky.c" "$TARGET/hyprland-focus-grab-v1-client-protocol.c" -lwayland-client -lpthread -ldl',
        "Recompile Wayland Click-Away Shared Library"
    ),
    FleetCommand(Mode.USER, 'systemctl --user daemon-reload || true', "Reload Systemd User Daemon"),
    FleetCommand(Mode.USER, 'systemctl --user restart osd_lock.service || true', "Restart OSD Lock Service"),
    FleetCommand(Mode.USER, 'update-desktop-database "$HOME/.local/share/applications" || true', "Update Desktop Entry Database Cache"),
]

# Register configured stages in default lifecycle order
STAGES: dict[str, list[FleetCommand]] = {
    "before": BEFORE_COMMANDS,
    "setup": SETUP_COMMANDS,
    "after": AFTER_COMMANDS,
}

# ==============================================================================
# 2. CONTEXT & ENVIRONMENT RESOLUTION
# ==============================================================================

@dataclass(frozen=True)
class UserContext:
    username: str
    home: Path
    uid: int
    gid: int
    is_root: bool


def resolve_user_context(is_json: bool = False) -> UserContext:
    """Resolves real non-root user details prioritizing active privilege escalation context."""
    is_root = os.geteuid() == 0
    real_uid = os.getuid()

    if is_root:
        escalation_uid = os.environ.get("SUDO_UID") or os.environ.get("PKEXEC_UID")
        if escalation_uid and escalation_uid.isdigit():
            real_uid = int(escalation_uid)
        elif "DOAS_USER" in os.environ:
            try:
                real_uid = pwd.getpwnam(os.environ["DOAS_USER"]).pw_uid
            except KeyError:
                pass
        else:
            try:
                loginuid_raw = Path("/proc/self/loginuid").read_text(encoding="utf-8").strip()
                loginuid = int(loginuid_raw)
                if loginuid != 4294967295:
                    real_uid = loginuid
            except (FileNotFoundError, ValueError, OSError):
                pass

    try:
        pw = pwd.getpwuid(real_uid)
    except KeyError:
        if is_json:
            print(json.dumps({"status": "fatal", "error": f"Resolved UID {real_uid} does not map to a valid user."}), file=sys.stderr)
        elif error_console:
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


@cache
def get_escalator() -> str:
    """Resolves available privilege escalation tool (sudo or doas)."""
    escalator = shutil.which("sudo") or shutil.which("doas")
    if not escalator:
        raise FileNotFoundError("Neither 'sudo' nor 'doas' found on system.")
    return escalator


def get_user_ipc_env(ctx: UserContext) -> dict[str, str]:
    """Build the user environment needed by Wayland and session services."""
    runtime_dir = Path(os.environ.get("XDG_RUNTIME_DIR") or f"/run/user/{ctx.uid}")
    env = {
        "PATH": os.environ.get("PATH", "/usr/local/sbin:/usr/local/bin:/usr/bin"),
        "USER": ctx.username,
        "HOME": str(ctx.home),
        "LANG": os.environ.get("LANG", "en_US.UTF-8"),
        "XDG_RUNTIME_DIR": str(runtime_dir),
    }

    dbus_path = runtime_dir / "bus"
    if dbus_path.exists():
        env["DBUS_SESSION_BUS_ADDRESS"] = f"unix:path={dbus_path}"

    # Preserve critical XDG, Wayland, and Hyprland IPC variables
    for xdg_var in (
        "XDG_CONFIG_HOME", "XDG_DATA_HOME", "XDG_SESSION_TYPE",
        "XDG_STATE_HOME", "DBUS_SESSION_BUS_ADDRESS", "LC_ALL", "LC_CTYPE",
        "WAYLAND_DISPLAY", "HYPRLAND_INSTANCE_SIGNATURE", "SSH_AUTH_SOCK"
    ):
        if xdg_var in os.environ:
            env[xdg_var] = os.environ[xdg_var]

    return env


# ==============================================================================
# 3. ENUMS & RESULT MODELS
# ==============================================================================

class ExecutionStatus(Enum):
    SUCCESS = "Success"
    SKIPPED = "Skipped"
    FAILED = "Failed"
    DRY_RUN = "Dry-Run"


class CommandResult(NamedTuple):
    stage: str
    command: FleetCommand
    status: ExecutionStatus
    message: str
    output: str = ""


# ==============================================================================
# 4. LOGGING & PRIVILEGE SUBSYSTEM
# ==============================================================================

class Logger:
    def __init__(self, log_path: Path, is_json: bool = False):
        self.log_path = log_path
        self.is_json = is_json
        self.log_path.parent.mkdir(parents=True, exist_ok=True)
        self._file = open(self.log_path, "a", encoding="utf-8")

        self.log("INFO", f"--- Dusky Commands Session Started: {time.strftime('%Y-%m-%d %H:%M:%S')} ---")

    def log(self, level: str, message: str) -> None:
        timestamp = time.strftime("%H:%M:%S")
        clean_msg = re.sub(r"\x1B(?:[@-Z\\-_]|\[[0-?]*[ -/]*[@-~])", "", message)
        if not self.is_json and console:
            styles = {"INFO": "blue", "SUCCESS": "green", "OK": "green",
                      "WARN": "yellow", "ERROR": "red", "RUN": "cyan"}
            target = error_console if level == "ERROR" else console
            target.print(f"[{level}] {clean_msg}", style=styles.get(level, ""), markup=False)
        self._file.write(f"[{timestamp}] [{level}] {clean_msg}\n")
        self._file.flush()

    def close(self) -> None:
        if not self._file.closed:
            self._file.close()


class SudoKeepAlive(threading.Thread):
    """Background daemon thread maintaining active sudo timestamp during execution."""

    def __init__(self, interval: int = 45):
        super().__init__(daemon=True)
        self.interval = interval
        self.stop_event = threading.Event()
        self.escalator = get_escalator()

    def run(self) -> None:
        while not self.stop_event.is_set():
            if self.stop_event.wait(self.interval):
                break
            try:
                result = subprocess.run(
                    [self.escalator, "-n", "-v"], stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL, timeout=10, check=False,
                )
                if result.returncode:
                    break
            except (OSError, subprocess.TimeoutExpired):
                break

    def stop(self) -> None:
        self.stop_event.set()


# ==============================================================================
# 5. ORCHESTRATION ENGINE
# ==============================================================================

class FleetPatcherEngine:
    def __init__(self, ctx: UserContext, is_json: bool = False) -> None:
        self.ctx = ctx
        self.is_json = is_json
        self.state_dir = ctx.home / ".local/state/dusky"
        self.state_file = self.state_dir / "patch_history.state"

        timestamp = time.strftime("%Y%m%d_%H%M%S")
        self.log_file = ctx.home / "Documents" / "logs" / f"dusky_patcher_{timestamp}.log"

        # One lock location for terminal, sudo, and session invocations.
        self.lock_file = self.state_dir / "dusky_fleet_patcher.lock"

        self.lock_fd: int | None = None
        self.completed_patches: set[str] = set()
        self.logger: Logger | None = None
        self.sudo_keepalive: SudoKeepAlive | None = None
        self.active_process: subprocess.Popen | None = None

    def acquire_lock(self) -> None:
        self.state_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
        try:
            self.lock_fd = os.open(self.lock_file, os.O_CREAT | os.O_RDWR, 0o600)
            fcntl.flock(self.lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            os.close(self.lock_fd)
            self.lock_fd = None
            raise RuntimeError("Another dusky fleet patcher instance is currently running.") from exc

    def load_state(self) -> None:
        self.completed_patches.clear()
        try:
            state = self.state_file.open("r", encoding="utf-8")
        except FileNotFoundError:
            return
        with state:
            self.completed_patches.update(line.strip() for line in state if line.strip())

    def record_completed(self, cmd_hash: str) -> None:
        """Atomic state write via temporary file replacement preventing corruption on reboot."""
        completed = self.completed_patches | {cmd_hash}
        tmp_file = self.state_file.with_suffix(".state.tmp")
        try:
            with tmp_file.open("w", encoding="utf-8") as f:
                f.write("\n".join(sorted(completed)) + "\n")
                f.flush()
                os.fsync(f.fileno())
            tmp_file.replace(self.state_file)
            directory_fd = os.open(self.state_dir, os.O_RDONLY | os.O_DIRECTORY)
            try:
                os.fsync(directory_fd)
            finally:
                os.close(directory_fd)
        finally:
            tmp_file.unlink(missing_ok=True)
        self.completed_patches = completed

    def ensure_sudo(self) -> bool:
        """Authenticate only immediately before a root command is executed."""
        if self.sudo_keepalive and self.sudo_keepalive.is_alive():
            return True

        if self.logger:
            self.logger.log("INFO", "Root privileges required for upcoming patches. Authenticating...")

        try:
            escalator = get_escalator()
            res = subprocess.run([escalator, "-n", "true"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            if res.returncode != 0:
                auth_args = [escalator, "-v"] if Path(escalator).name == "sudo" else [escalator, "true"]
                auth_res = subprocess.run(auth_args, stdout=subprocess.DEVNULL, timeout=60)
                if auth_res.returncode != 0:
                    if self.logger:
                        self.logger.log("ERROR", f"{Path(escalator).name.capitalize()} authentication failed. Cannot apply root patches.")
                    return False
        except OSError as exc:
            if self.logger:
                self.logger.log("ERROR", f"Privilege authentication unavailable: {exc}")
            return False
        except subprocess.TimeoutExpired:
            if self.logger:
                self.logger.log("ERROR", f"{Path(escalator).name.capitalize()} authentication timed out.")
            return False

        if Path(escalator).name == "sudo":
            self.sudo_keepalive = SudoKeepAlive()
            self.sudo_keepalive.start()
        return True

    def run_stage(
        self,
        stage_name: str,
        commands: list[FleetCommand],
        force: bool = False,
        dry_run: bool = False,
        interactive: bool = False,
        use_defaults: bool = False,
    ) -> list[CommandResult]:
        results: list[CommandResult] = []
        if not commands:
            return results

        if self.logger:
            self.logger.log("INFO", f"=== Stage: {stage_name.upper()} ({len(commands)} commands) ===")

        pending = commands if force else [c for c in commands if c.state_hash not in self.completed_patches]
        if not pending:
            if self.logger:
                self.logger.log("SUCCESS", f"All patches in stage '{stage_name}' are already up to date.")
            for c in commands:
                results.append(CommandResult(stage_name, c, ExecutionStatus.SKIPPED, "Already applied"))
            return results

        env = get_user_ipc_env(self.ctx)
        total = len(commands)

        for idx, cmd_obj in enumerate(commands, start=1):
            cmd_hash = cmd_obj.state_hash
            is_done = cmd_hash in self.completed_patches

            if is_done and not force:
                if self.logger:
                    self.logger.log("INFO", f"[{idx}/{total}] Skipping (Already applied): {cmd_obj.cmd}")
                results.append(CommandResult(stage_name, cmd_obj, ExecutionStatus.SKIPPED, "Already applied"))
                continue

            if interactive and not use_defaults and not self.is_json and sys.stdin.isatty() and Confirm:
                prompt_msg = f"Execute [{cmd_obj.mode.value}] patch '{cmd_obj.description or cmd_obj.cmd}'?"
                if not Confirm.ask(Text(prompt_msg), default=True):
                    if self.logger:
                        self.logger.log("INFO", f"[{idx}/{total}] Skipped by user: {cmd_obj.cmd}")
                    results.append(CommandResult(stage_name, cmd_obj, ExecutionStatus.SKIPPED, "Skipped by user prompt"))
                    continue

            if dry_run:
                message = f"[{idx}/{total}] [DRY-RUN] Would apply [{cmd_obj.mode.value}]: {cmd_obj.cmd}"
                if self.logger:
                    self.logger.log("RUN", message)
                elif not self.is_json and console:
                    console.print(message, markup=False)
                results.append(CommandResult(stage_name, cmd_obj, ExecutionStatus.DRY_RUN, "Dry-run simulation"))
                continue

            if self.logger:
                self.logger.log("RUN", f"[{idx}/{total}] Applying [{cmd_obj.mode.value}]: {cmd_obj.cmd}")

            if cmd_obj.mode == Mode.SUDO and not self.ensure_sudo():
                results.append(CommandResult(stage_name, cmd_obj, ExecutionStatus.FAILED, "Privilege authentication failed"))
                continue

            exec_cmd = ["/usr/bin/bash", "-c", f"set -eo pipefail; {cmd_obj.cmd}"]
            if cmd_obj.mode == Mode.SUDO:
                escalator = get_escalator()
                exec_cmd = [escalator, "-n", *exec_cmd]

            try:
                # A separate process group supports cleanup while retaining the session
                # required by sudo's terminal-scoped authentication cache.
                self.active_process = subprocess.Popen(
                    exec_cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, env=env, text=True, errors="replace", process_group=0
                )
                stdout_data, _ = self.active_process.communicate()
                ret_code = self.active_process.returncode
                output_text = stdout_data.strip() if stdout_data else ""

                if self.logger and output_text:
                    self.logger.log("OUTPUT", output_text)

                if ret_code == 0:
                    self.record_completed(cmd_hash)
                    if self.logger:
                        self.logger.log("SUCCESS", "Patch applied successfully.")
                    results.append(CommandResult(stage_name, cmd_obj, ExecutionStatus.SUCCESS, "Patch applied successfully", output_text))
                else:
                    if self.logger:
                        self.logger.log("WARN", f"Patch failed with exit code {ret_code}: {cmd_obj.cmd}")
                        self.logger.log("WARN", "Continuing orchestration sequence despite failure...")
                    results.append(CommandResult(stage_name, cmd_obj, ExecutionStatus.FAILED, f"Failed with exit code {ret_code}", output_text))

            except (OSError, subprocess.SubprocessError) as e:
                if self.logger:
                    self.logger.log("ERROR", f"Subprocess execution crashed:\n{traceback.format_exc()}")
                results.append(CommandResult(stage_name, cmd_obj, ExecutionStatus.FAILED, f"Exception: {e}", ""))
            finally:
                # Keep the process reference on signals so cleanup can stop its group.
                if self.active_process and self.active_process.returncode is not None:
                    if self.active_process.stdout:
                        self.active_process.stdout.close()
                    self.active_process = None

        return results

    def cleanup(self) -> None:
        if self.sudo_keepalive:
            self.sudo_keepalive.stop()

        try:
            if self.active_process:
                process = self.active_process
                # process_group=0 makes the child's PID its process group ID.
                pgid = process.pid

                def send_group(sig: signal.Signals) -> None:
                    try:
                        os.killpg(pgid, sig)
                    except ProcessLookupError:
                        pass
                    except PermissionError:
                        subprocess.run(
                            [get_escalator(), "-n", "/usr/bin/kill", f"-{sig.name.removeprefix('SIG')}", "--", f"-{pgid}"],
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=10, check=False,
                        )

                try:
                    send_group(signal.SIGTERM)
                    try:
                        process.wait(timeout=2)
                    except subprocess.TimeoutExpired:
                        pass
                    send_group(signal.SIGKILL)
                    process.wait(timeout=2)
                finally:
                    if process.stdout:
                        process.stdout.close()
                    self.active_process = None

        finally:
            if self.sudo_keepalive:
                self.sudo_keepalive.join(timeout=11)
                self.sudo_keepalive = None
            if self.logger:
                self.logger.close()
            if self.lock_fd is not None:
                os.close(self.lock_fd)
                self.lock_fd = None
                # Keep the lock inode so concurrent instances always lock the same file.


# ==============================================================================
# 6. RICH PRESENTATION & CLI RENDERING
# ==============================================================================

def render_header(ctx: UserContext, selected_stages: list[str]) -> None:
    """Renders the main Dusky Commands header panel."""
    if not console:
        return
    header_text = Text()
    header_text.append("⚡ DUSKY COMMANDS — Bleeding-Edge Multi-Stage Fleet Orchestrator ⚡\n", style="bold cyan")
    header_text.append("Context: Arch Linux (Bleeding-Edge) | Kernel: ", style="dim white")
    header_text.append(f"{os.uname().release} | User: ", style="bold yellow")
    header_text.append(ctx.username, style="bold green")
    header_text.append(f" (Root: {ctx.is_root})\n", style="dim cyan")
    header_text.append(f"Target Stages: {', '.join(s.upper() for s in selected_stages)}", style="bold magenta")

    console.print(Panel(header_text, expand=False, border_style="cyan"))


def render_status_matrix(engine: FleetPatcherEngine, selected_stages: list[str]) -> None:
    """Displays a status matrix of all configured commands and their applied state."""
    if not console:
        return
    table = Table(title="Dusky Commands — Patch History & State Matrix", show_lines=True)
    table.add_column("Stage", style="bold cyan")
    table.add_column("Mode", style="bold yellow", justify="center")
    table.add_column("Applied", justify="center", width=12)
    table.add_column("Command", style="white")
    table.add_column("Description", style="dim green")

    for stage_name in selected_stages:
        cmds = STAGES.get(stage_name, [])
        for cmd_obj in cmds:
            mode_badge = "[bold red]SUDO[/bold red]" if cmd_obj.mode == Mode.SUDO else "[bold green]USER[/bold green]"
            is_applied = cmd_obj.state_hash in engine.completed_patches
            status_fmt = "[green]✔ YES[/green]" if is_applied else "[yellow]○ PENDING[/yellow]"
            table.add_row(stage_name.upper(), mode_badge, status_fmt, Text(cmd_obj.cmd), Text(cmd_obj.description or "-"))

    console.print(table)


def list_commands() -> None:
    """Displays all configured stages and fleet commands in a Rich table."""
    if not console:
        return
    table = Table(title="Dusky Commands — Configured Stages & Fleet Commands", show_lines=True)
    table.add_column("Stage", style="bold cyan")
    table.add_column("Mode", style="bold yellow", justify="center")
    table.add_column("Command", style="white")
    table.add_column("Description", style="dim green")

    for stage_name, cmds in STAGES.items():
        for cmd_obj in cmds:
            mode_badge = "[bold red]SUDO[/bold red]" if cmd_obj.mode == Mode.SUDO else "[bold green]USER[/bold green]"
            table.add_row(stage_name.upper(), mode_badge, Text(cmd_obj.cmd), Text(cmd_obj.description or "-"))

    console.print(table)


def render_execution_summary(all_results: list[CommandResult]) -> None:
    """Displays a summary panel of execution results."""
    if not console:
        return

    success_cnt = sum(1 for r in all_results if r.status == ExecutionStatus.SUCCESS)
    skipped_cnt = sum(1 for r in all_results if r.status == ExecutionStatus.SKIPPED)
    failed_cnt = sum(1 for r in all_results if r.status == ExecutionStatus.FAILED)
    dry_cnt = sum(1 for r in all_results if r.status == ExecutionStatus.DRY_RUN)

    summary_text = (
        f"[bold green]Applied/Success: {success_cnt}[/bold green] | "
        f"[bold blue]Skipped/Up-to-Date: {skipped_cnt}[/bold blue] | "
        f"[bold yellow]Dry-Run: {dry_cnt}[/bold yellow] | "
        f"[bold red]Failures: {failed_cnt}[/bold red]"
    )
    console.print(Panel(summary_text, title="Orchestration Execution Summary", border_style="bright_blue"))


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Dusky Commands — Bleeding-Edge Multi-Stage Fleet Orchestrator",
        formatter_class=argparse.RawTextHelpFormatter,
    )

    stage_group = parser.add_argument_group("Stage Selection Flags (Can combine multiple)")
    stage_group.add_argument("-b", "--before", action="store_true", help="Run Pre-Update stage commands")
    stage_group.add_argument("-s", "--setup", action="store_true", help="Run Setup / Post-Install stage commands")
    stage_group.add_argument("-a", "--after", action="store_true", help="Run Post-Update stage commands")
    stage_group.add_argument("-A", "--all", action="store_true", help="Run ALL stages in sequence (before -> setup -> after)")
    stage_group.add_argument("--stage", choices=[*STAGES, "all"], help="Specify a single target stage or 'all'")

    control_group = parser.add_argument_group("Execution Control Flags")
    control_group.add_argument("-i", "--interactive", action="store_true", help="Interactively prompt before executing each patch")
    control_group.add_argument("-y", "--default", action="store_true", help="Non-interactive mode (auto-apply default choices)")
    control_group.add_argument("-f", "--force", action="store_true", help="Force re-execution of commands even if previously completed")
    inspection_group = control_group.add_mutually_exclusive_group()
    inspection_group.add_argument("-n", "--dry-run", action="store_true", help="Preview planned command executions without making changes")
    inspection_group.add_argument("-st", "--status", action="store_true", help="Inspect and display patch state matrix without executing")
    inspection_group.add_argument("-l", "--list", action="store_true", help="Display all configured stages and commands in a Rich table")
    control_group.add_argument("--json", action="store_true", help="Output status/execution results as formatted JSON")
    inspection_group.add_argument("--reset-state", action="store_true", help="Reset and clear state history file")

    return parser.parse_args()


# ==============================================================================
# 7. MAIN ENTRYPOINT
# ==============================================================================

def main() -> int:
    args = parse_args()
    if args.json:
        sys.excepthook = _json_excepthook
    check_ui_deps(args.json)

    if args.list:
        if args.json:
            print(json.dumps([
                {"stage": stage, "mode": cmd.mode.value, "cmd": cmd.cmd,
                 "description": cmd.description, "hash": cmd.state_hash}
                for stage, commands in STAGES.items() for cmd in commands
            ], indent=2))
        else:
            list_commands()
        return 0

    ctx = resolve_user_context(is_json=args.json)
    if ctx.uid == 0:
        raise RuntimeError("Run as a normal user, or through sudo with an underlying normal user.")
    if ctx.is_root:
        # Run the orchestrator as the resolved user so state, logs, and USER patches
        # have the same ownership and behavior as a normal invocation.
        os.initgroups(ctx.username, ctx.gid)
        os.setgid(ctx.gid)
        os.setuid(ctx.uid)
        os.environ.update(HOME=str(ctx.home), USER=ctx.username, LOGNAME=ctx.username,
                          XDG_RUNTIME_DIR=f"/run/user/{ctx.uid}")
        os.environ.pop("DBUS_SESSION_BUS_ADDRESS", None)

    selected_stages = [
        stage for stage in STAGES
        if args.all or args.stage == "all" or getattr(args, stage, False) or args.stage == stage
    ] or list(STAGES)
    engine = FleetPatcherEngine(ctx, is_json=args.json)

    def handle_exit(signum: int, frame: Any) -> None:
        raise SystemExit(128 + signum)

    old_handlers = {sig: signal.signal(sig, handle_exit) for sig in (signal.SIGINT, signal.SIGTERM)}
    try:
        # Inspection and simulation only read the atomically replaced history.
        if not (args.status or args.dry_run) or args.reset_state:
            engine.acquire_lock()
        if args.reset_state:
            existed = engine.state_file.exists()
            engine.state_file.unlink(missing_ok=True)
            message = "State history cleared successfully" if existed else "No state file found to clear"
            if args.json:
                print(json.dumps({"status": "success" if existed else "warning", "message": message}))
            else:
                console.print(message, markup=False)
            return 0

        engine.load_state()
        if args.status:
            if args.json:
                print(json.dumps([
                    {"stage": stage, "mode": cmd.mode.value, "cmd": cmd.cmd,
                     "description": cmd.description, "hash": cmd.state_hash,
                     "applied": cmd.state_hash in engine.completed_patches}
                    for stage in selected_stages for cmd in STAGES[stage]
                ], indent=2))
            else:
                render_status_matrix(engine, selected_stages)
            return 0

        if not args.dry_run:
            engine.logger = Logger(engine.log_file, is_json=args.json)
        if not args.json:
            render_header(ctx, selected_stages)

        all_results = [
            result
            for stage in selected_stages
            for result in engine.run_stage(
                stage, STAGES[stage], force=args.force, dry_run=args.dry_run,
                interactive=args.interactive, use_defaults=args.default,
            )
        ]
        failed = any(result.status == ExecutionStatus.FAILED for result in all_results)
        if args.json:
            print(json.dumps([
                {"stage": result.stage, "mode": result.command.mode.value,
                 "cmd": result.command.cmd, "status": result.status.value,
                 "message": result.message, "output": result.output}
                for result in all_results
            ], indent=2))
        else:
            render_execution_summary(all_results)
        if engine.logger:
            engine.logger.log("WARN" if failed else "INFO", "Execution finished with failures." if failed else "Execution finished.")
        return int(failed)
    except (SystemExit, KeyboardInterrupt) as exc:
        if args.json:
            print(json.dumps({"status": "aborted", "message": "Interrupted by signal"}), file=sys.stderr)
        else:
            error_console.print("[ABORTED] Interrupted by user/system signal.", style="red", markup=False)
        return exc.code if isinstance(exc, SystemExit) else 130
    finally:
        # Ignore repeated termination signals while reaping children and releasing resources.
        for sig in old_handlers:
            signal.signal(sig, signal.SIG_IGN)
        try:
            engine.cleanup()
        finally:
            for sig, handler in old_handlers.items():
                signal.signal(sig, handler)


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        if console:
            console.print("\n[bold red][ABORTED][/bold red] Interrupted by user (SIGINT).")
        else:
            print("\n[ABORTED] Interrupted by user (SIGINT).", file=sys.stderr)
        sys.exit(130)
