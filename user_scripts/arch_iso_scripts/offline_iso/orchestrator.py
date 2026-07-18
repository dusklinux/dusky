#!/usr/bin/env python3
# ==============================================================================
#  ARCH LINUX ISO TEXTUAL ORCHESTRATOR (Asynchronous Engine + Profile Support)
# ==============================================================================
# A Textual-based UI for executing the sequenced series of installation scripts.
# Supports loading custom installation profile TOML configurations dynamically.
# ==============================================================================

import os
import sys
import subprocess
import time
import fcntl
import hashlib
import shlex
import argparse
import shutil
import asyncio
import tomllib
from pathlib import Path
from dataclasses import dataclass
from typing import List, Dict, Optional, Tuple

from rich.console import Console
from rich.text import Text

from textual.app import App, ComposeResult
from textual.containers import Container, Horizontal, Vertical
from textual.widgets import Header, Footer, Static, RichLog, ProgressBar
from textual import work

# ==============================================================================
#  CONSTANTS & FALLBACK SEQUENCES
# ==============================================================================
SCRIPT_DIR = Path(__file__).resolve().parent
PROFILES_DIR = SCRIPT_DIR / "profiles"

# Hardcoded sequences corresponding to Phase 1 (ISO) and Phase 2 (Chroot)
# Used as robust fallbacks if no TOML profiles are discovered.
FALLBACK_ISO_SEQUENCE = [
    ("001_uefi_check.sh", [], False, False),
    ("010_set_variables.sh", ["--no_encrypt"], False, True),  # Wizard: interactive
    ("020_environment_prep.sh", ["--auto", "--cachy"], False, False),
    ("030_partitioning.py", ["--no-encrypt"], False, True),   # Arrow menu: interactive
    ("040_disk_mount.py", ["--auto"], False, False),
    ("045_repo_bind_mount.sh", [], False, False),
    ("051_pacman_repo_switch.sh", ["--offline", "--cachyos"], False, False),
    ("060_console_fix.sh", [], False, False),
    ("070_pacstrap_and_disable_mkinitcpio.py", ["--auto", "--cachyos"], False, False),
    ("080_script_directories_population_in_chroot.sh", [], False, False),
    ("090_fstab.sh", ["--auto"], False, False),
]

FALLBACK_CHROOT_SEQUENCE = [
    ("051_pacman_repo_switch.sh", ["--offline", "--cachyos"], False, False),
    ("100_etc_skel.sh", ["--auto"], False, False),
    ("101_skel_files_precision_edit.sh", ["--inject"], False, False),
    ("103_configure_hyprland_gpu.py", ["--auto"], False, False),
    ("110_post_chroot.sh", ["--auto"], False, False),
    ("115_tty_autologin.sh", ["--auto"], False, False),
    ("120_mkinitcpio_optimizer.sh", [], True, False),
    ("135_plymouth_setup.py", [], False, False),
    ("130_chroot_package_installer.sh", ["--auto", "--cachyos"], False, False),
    ("131_chroot_aur_packages.sh", ["--auto", "--cachyos"], False, False),
    ("151_systemd_bootloader.py", [], False, False),
    ("156_snapper_isolation_subvolume.sh", ["--auto"], False, False),
    ("158_mkinitcpio_restore_and_generate.sh", [], False, False),
    ("160_zram_config.sh", [], False, False),
    ("165_deploy_dusky_run.py", [], False, False),
    ("170_services.sh", [], False, False),
    ("051_pacman_repo_switch.sh", ["--online", "--cachyos"], False, False),
]

THEME = {
    "info": "cyan",
    "success": "green",
    "warning": "yellow",
    "error": "red",
    "run": "bold white",
    "done": "green",
    "missing": "red",
    "pending": "blue"
}

# ==============================================================================
#  DATA CLASSES
# ==============================================================================
@dataclass
class OrchestratorTask:
    script_name: str
    args: List[str]
    ignore_fail: bool
    interactive: bool
    interpreter: str = "bash"
    state_key: str = ""
    resolved_path: Optional[Path] = None

@dataclass
class ProfileConfig:
    filepath: Optional[Path]
    name: str
    description: str
    phase1_tasks: List[OrchestratorTask]
    phase2_tasks: List[OrchestratorTask]

# ==============================================================================
#  TOML PARSER & PROFILE ENGINE
# ==============================================================================
def parse_task_entry(raw_entry: str) -> OrchestratorTask:
    parts = [p.strip() for p in raw_entry.split("|")]
    if len(parts) == 2:
        mode, cmd = parts
        flags = ""
    elif len(parts) == 3:
        mode, flags, cmd = parts
    else:
        raise ValueError(f"Malformed entry: {raw_entry}")

    ignore_fail = False
    interactive = False
    for flag in flags.split(","):
        flag = flag.strip().lower()
        if flag in ["true", "ignore", "ignore-fail"]:
            ignore_fail = True
        elif flag in ["interactive", "tui", "prompt"]:
            interactive = True

    cmd_tokens = shlex.split(cmd)
    if not cmd_tokens:
        raise ValueError(f"Empty command in entry: {raw_entry}")

    if cmd_tokens[0] == "true" and len(cmd_tokens) > 1:
        ignore_fail = True
        cmd_tokens = cmd_tokens[1:]

    return OrchestratorTask(
        script_name=cmd_tokens[0],
        args=cmd_tokens[1:],
        ignore_fail=ignore_fail,
        interactive=interactive
    )

def load_profile(filepath: Path) -> ProfileConfig:
    with open(filepath, "rb") as f:
        data = tomllib.load(f)

    p_data = data.get("profile", {})
    ph1_data = data.get("phase1", {})
    ph2_data = data.get("phase2", {})

    p1_tasks = []
    for line in ph1_data.get("scripts", []):
        try:
            p1_tasks.append(parse_task_entry(line))
        except ValueError as e:
            sys.stderr.write(f"Error parsing profile {filepath.name} [phase1]: {e}\n")
            sys.exit(1)

    p2_tasks = []
    for line in ph2_data.get("scripts", []):
        try:
            p2_tasks.append(parse_task_entry(line))
        except ValueError as e:
            sys.stderr.write(f"Error parsing profile {filepath.name} [phase2]: {e}\n")
            sys.exit(1)

    return ProfileConfig(
        filepath=filepath,
        name=p_data.get("name", filepath.stem),
        description=p_data.get("description", ""),
        phase1_tasks=p1_tasks,
        phase2_tasks=p2_tasks
    )

def discover_profiles() -> List[ProfileConfig]:
    if not PROFILES_DIR.exists():
        return []
    profiles = []
    for f in sorted(PROFILES_DIR.glob("*.toml")):
        try:
            profiles.append(load_profile(f))
        except Exception as e:
            sys.stderr.write(f"Warning: Failed to load profile {f.name}: {e}\n")
    return profiles

# ==============================================================================
#  CLI ARGUMENTS & LOCKING
# ==============================================================================
def parse_args():
    parser = argparse.ArgumentParser(description="Dusky Arch ISO Textual Orchestrator")
    parser.add_argument("--phase1", action="store_true", help="Run Phase 1 (ISO Environment)")
    parser.add_argument("--phase2", action="store_true", help="Run Phase 2 (Chroot Environment)")
    parser.add_argument("--reset", action="store_true", help="Reset execution state for the current phase")
    parser.add_argument("--dry-run", action="store_true", help="Dry run: validate scripts presence and exit")
    parser.add_argument("--force", action="store_true", help="Pass --force flag to subscripts")
    parser.add_argument("--manual", "-m", action="store_true", help="Manual mode: prompt before each script")
    parser.add_argument("--stop-on-fail", action="store_true", help="Halt execution if any script fails (Auto mode)")
    parser.add_argument("--profile", type=str, help="Specify profile TOML to execute (name or filename stem)")
    parser.add_argument("--list-profiles", action="store_true", help="List all available installer profiles and exit")
    return parser.parse_args()

def get_lock_holders(lock_file: Path) -> str:
    if not lock_file.exists():
        return ""
    try:
        real_lock = lock_file.resolve()
    except Exception:
        return ""

    holders = []
    seen = set()
    proc_dir = Path("/proc")
    if not proc_dir.exists():
        return ""

    for pid_dir in proc_dir.iterdir():
        if not pid_dir.name.isdigit(): continue
        pid = pid_dir.name
        if pid == str(os.getpid()): continue
        if pid in seen: continue

        fd_dir = pid_dir / "fd"
        if not fd_dir.exists() or not os.access(fd_dir, os.R_OK): continue

        try:
            for fd_link in fd_dir.iterdir():
                try:
                    target = fd_link.resolve()
                    if target == real_lock:
                        seen.add(pid)
                        cmdline_path = pid_dir / "cmdline"
                        cmd = ""
                        if cmdline_path.exists():
                            cmd = cmdline_path.read_text(errors='replace').replace('\x00', ' ').strip()
                        if not cmd:
                            cmd = f"[pid {pid}]"
                        holders.append(f"  - PID {pid}: {cmd}")
                        break
                except Exception:
                    pass
        except Exception:
            pass

    return "\n".join(holders)

def acquire_lock(lock_file: Path) -> bool:
    lock_file.parent.mkdir(parents=True, exist_ok=True)
    try:
        fd = os.open(str(lock_file), os.O_CREAT | os.O_RDWR, 0o600)
    except Exception as e:
        sys.stderr.write(f"\033[1;31m[ERROR]\033[0m Could not open lock file {lock_file}: {e}\n")
        return False

    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        return True
    except BlockingIOError:
        sys.stdout.write(f"\033[1;31m[ERROR]\033[0m Another instance is already running.\n")
        holders = get_lock_holders(lock_file)
        if holders:
            sys.stdout.write(f"{holders}\n")
        else:
            sys.stdout.write("\033[1;33m[WARN]\033[0m No live lock holder identified. Stale lock?\n")
            try:
                sys.stdout.write("Attempting to acquire stale lock...\n")
                fcntl.flock(fd, fcntl.LOCK_EX)
                return True
            except Exception:
                sys.stdout.write("\033[1;31m[ERROR]\033[0m Failed to acquire lock.\n")
                return False
        return False

def resolve_interpreter(script_path: Path) -> str:
    try:
        with open(script_path, 'r', errors='ignore') as f:
            first_line = f.readline().strip()
        if "python" in first_line:
            return "python"
        elif any(x in first_line for x in ["bash", "sh", "zsh"]):
            return "bash"
    except Exception:
        pass
    
    if script_path.suffix == '.py':
        return "python"
    return "bash"

def is_script_interactive(script_path: Path) -> bool:
    if not script_path.exists() or not script_path.is_file():
        return False
    try:
        with open(script_path, 'r', errors='ignore') as f:
            for _ in range(20):
                line = f.readline()
                if not line:
                    break
                line_clean = line.strip().replace(" ", "").lower()
                if "#dusky_interactive=true" in line_clean or "#dusky_interactive=1" in line_clean:
                    return True
    except Exception:
        pass
    return False

# ==============================================================================
#  TEXTUAL APP
# ==============================================================================
class DuskyOrchestratorApp(App):
    CSS = """
    Screen {
        background: $surface;
        layout: vertical;
    }
    #header {
        height: 3;
        dock: top;
        content-align: center middle;
        background: $primary-darken-2;
        color: $text;
        text-style: bold;
    }
    #progress_bar {
        margin: 0 2;
    }
    #main_content {
        layout: horizontal;
        height: 1fr;
    }
    #left_pane {
        width: 35%;
        border-right: vkey $primary-darken-1;
        padding: 0 1;
        height: 100%;
        overflow-y: auto;
    }
    #right_pane {
        width: 65%;
        height: 100%;
        padding: 0 1;
        background: $surface-darken-1;
    }
    .task_row {
        layout: horizontal;
        height: 1;
        margin-bottom: 0;
    }
    .task_icon { width: 3; text-align: center; }
    .task_mode { width: 5; text-align: center; color: $warning; }
    .task_name { width: 1fr; }
    
    RichLog {
        height: 100%;
        border: none;
        scrollbar-size: 1 1;
    }
    #footer {
        dock: bottom;
        height: 1;
        background: $primary-darken-3;
        color: $text-muted;
    }
    """

    def __init__(self, tasks: List[OrchestratorTask], phase_title: str, profile_name: str, state_file: Path, manual: bool, stop_on_fail: bool, force: bool):
        super().__init__()
        self.tasks = tasks
        self.phase_title = phase_title
        self.profile_name = profile_name
        self.state_file = state_file
        self.manual = manual
        self.stop_on_fail = stop_on_fail
        self.force_flag = force
        
        self.current_idx = 0
        self.completed_keys = set()
        
        if self.state_file.exists():
            try:
                self.completed_keys = set(self.state_file.read_text().splitlines())
            except Exception:
                pass

        self.log_widget = RichLog(id="syslog", highlight=True, markup=False)
        self.left_pane = Vertical(id="left_pane")
        self.progress_bar = ProgressBar(total=len(self.tasks), show_eta=False, id="progress_bar")

        self.waiting_for_input = False
        self.input_action = None

    def compose(self) -> ComposeResult:
        with Horizontal(id="header"):
            yield Static(f"◈ DUSKY ARCH INSTALLER  [{self.phase_title}]  (Profile: {self.profile_name})", classes="title")
            yield self.progress_bar
            
        with Horizontal(id="main_content"):
            yield self.left_pane
            with Vertical(id="right_pane"):
                yield self.log_widget
                
        yield Footer()

    def on_mount(self) -> None:
        self.render_task_list()
        
        for t in self.tasks:
            if t.state_key in self.completed_keys:
                self.progress_bar.advance(1)
                
        self.log_system(f"Started Installation Phase: {self.phase_title}")
        self.log_system(f"Active Profile: {self.profile_name}")
        self.log_system(f"Loaded Phase State: {len(self.completed_keys)} completed tasks cached")
        
        # Start execution loop as an async worker
        self.run_worker(self.run_execution_loop())

    def render_task_list(self):
        self.left_pane.remove_children()
        for i, t in enumerate(self.tasks):
            if t.state_key in self.completed_keys:
                icon = f"[{THEME['done']}]✓[/]"
            elif not t.resolved_path:
                icon = f"[{THEME['missing']}]![/]"
            elif i == self.current_idx:
                icon = f"[{THEME['info']}]◉[/]"
            elif i < self.current_idx:
                icon = f"[{THEME['error']}]x[/]"
            else:
                icon = f"[{THEME['pending']}]·[/]"
                
            name = t.script_name[:25]
            row = Horizontal(
                Static(icon, classes="task_icon"),
                Static("ROOT", classes="task_mode"),
                Static(name, classes="task_name"),
                classes="task_row",
                id=f"row_{i}"
            )
            self.left_pane.mount(row)

    def update_task_icon(self, idx: int, status: str):
        try:
            row = self.query_one(f"#row_{idx}")
            icon_w = row.children[0]
            if status == "running":
                icon_w.update(f"[{THEME['info']}]◉[/]")
            elif status == "done":
                icon_w.update(f"[{THEME['done']}]✓[/]")
            elif status == "failed":
                icon_w.update(f"[{THEME['error']}]x[/]")
        except Exception:
            pass

    def log_system(self, msg: str):
        self.log_widget.write(Text.from_ansi(f"\033[1;36m[SYSTEM]\033[0m {msg}"))

    def log_task(self, msg: str):
        self.log_widget.write(Text.from_ansi(msg))

    def log_header(self, task: OrchestratorTask):
        self.log_widget.write(Text.from_ansi(f"\n\033[1;36m>>> PROCESS INITIATED: {task.script_name}\033[0m"))

    async def run_execution_loop(self):
        while self.current_idx < len(self.tasks):
            task = self.tasks[self.current_idx]
            
            if task.state_key in self.completed_keys:
                self.current_idx += 1
                continue
                
            if not task.resolved_path:
                self.handle_missing_task(task)
                return
                
            if self.manual:
                self.prompt_manual_continue(task)
                return
                
            await self.execute_current_task()
            return

        self.log_system("All phase tasks completed successfully!")
        await asyncio.sleep(2)
        self.exit(0)

    def handle_missing_task(self, task: OrchestratorTask):
        self.update_task_icon(self.current_idx, "failed")
        self.log_task(f"\033[1;31m[ERROR] Missing script: {task.script_name}\033[0m")
        self.prompt_retry_skip("Script missing. Skip [s] or Quit [q]?")

    def prompt_manual_continue(self, task: OrchestratorTask):
        self.update_task_icon(self.current_idx, "running")
        self.log_header(task)
        self.log_system("Manual confirmation required: Proceed [y] / Skip [s] / Quit [q]?")
        self.waiting_for_input = True
        self.input_action = 'manual'

    def prompt_retry_skip(self, msg: str):
        self.log_system(msg)
        self.waiting_for_input = True
        self.input_action = 'retry_skip'

    async def execute_current_task(self):
        task = self.tasks[self.current_idx]
        self.update_task_icon(self.current_idx, "running")
        self.log_header(task)
        
        args = list(task.args)
        if self.force_flag and "--force" not in args:
            args.append("--force")
            
        cmd = [task.interpreter, str(task.resolved_path)] + args
        
        if task.interactive:
            # INTERACTIVE MODE: Suspend UI so the terminal takes over control natively
            self.log_system("Suspending TUI... delegating terminal to interactive wizard.")
            await asyncio.sleep(0.5)  # Let logs flush
            with self.suspend():
                # Run subprocess directly sharing stdout/stdin/stderr
                rc = subprocess.run(cmd).returncode
            self.log_system(f"TUI Resumed. Script exited with code: {rc}")
            
            if rc == 0:
                self.task_success(task)
            else:
                self.task_failure(task, rc)
        else:
            # NON-INTERACTIVE MODE: Run as async process, piping output to Textual RichLog
            try:
                proc = await asyncio.create_subprocess_exec(
                    *cmd,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.STDOUT
                )
                
                if proc.stdout:
                    while True:
                        line = await proc.stdout.readline()
                        if not line:
                            break
                        decoded = line.decode('utf-8', errors='replace')
                        self.log_task(decoded)
                        
                rc = await proc.wait()
                
                if rc == 0:
                    self.task_success(task)
                else:
                    if task.ignore_fail:
                        self.log_system(f"Task exited with status {rc} but is marked to ignore failure. Continuing.")
                        self.task_success(task)
                    else:
                        self.task_failure(task, rc)
            except Exception as e:
                self.task_failure(task, str(e))

    def task_success(self, task: OrchestratorTask):
        self.update_task_icon(self.current_idx, "done")
        self.log_task("\n\033[1;32m>>> EXECUTION SUCCESSFUL\033[0m")
        self.completed_keys.add(task.state_key)
        
        try:
            with open(self.state_file, "a") as f:
                f.write(task.state_key + "\n")
        except Exception as e:
            self.log_system(f"Failed to record state: {e}")
            
        self.progress_bar.advance(1)
        self.current_idx += 1
        self.run_worker(self.run_execution_loop())

    def task_failure(self, task: OrchestratorTask, reason):
        self.update_task_icon(self.current_idx, "failed")
        self.log_task(f"\n\033[1;31m>>> EXECUTION FAILED: {reason}\033[0m")
        if self.stop_on_fail:
            self.log_system("stop-on-fail active. Aborting phase.")
            self.set_timer(2.0, self.action_quit_app)
        else:
            self.prompt_retry_skip("Task Failed. Retry [r] / Skip [s] / Quit [q]?")

    def action_quit_app(self):
        self.exit(1)
        
    def action_skip_task(self):
        if self.waiting_for_input and self.input_action in ('manual', 'retry_skip'):
            self.waiting_for_input = False
            self.log_system("Skipping task.")
            self.update_task_icon(self.current_idx, "failed")
            self.current_idx += 1
            self.progress_bar.advance(1)
            self.run_worker(self.run_execution_loop())

    def action_retry_task(self):
        if self.waiting_for_input and self.input_action == 'retry_skip':
            self.waiting_for_input = False
            self.log_system("Retrying task...")
            self.run_worker(self.run_execution_loop())
            
    def action_yes(self):
        if self.waiting_for_input and self.input_action == 'manual':
            self.waiting_for_input = False
            self.run_worker(self.execute_current_task())

    def action_no(self):
        if self.waiting_for_input and self.input_action in ('manual', 'retry_skip'):
            self.waiting_for_input = False
            self.log_system("Aborting installation.")
            self.exit(1)

    def on_key(self, event) -> None:
        if self.waiting_for_input:
            key = event.key.lower()
            if key == "q":
                self.action_quit_app()
            elif key == "s":
                self.action_skip_task()
            elif key == "r":
                self.action_retry_task()
            elif key == "y":
                self.action_yes()
            elif key == "n":
                self.action_no()

# ==============================================================================
#  MAIN ENTRY
# ==============================================================================
if __name__ == "__main__":
    args = parse_args()
    
    phase2 = args.phase2
    phase1 = args.phase1
    
    if not phase1 and not phase2:
        try:
            root_stat = os.stat("/")
            proc_root_stat = os.stat("/proc/1/root/.")
            if root_stat.st_dev != proc_root_stat.st_dev or root_stat.st_ino != proc_root_stat.st_ino:
                phase2 = True
            else:
                phase1 = True
        except Exception:
            phase1 = True

    # 1. Discover Profiles
    profiles = discover_profiles()
    
    if args.list_profiles:
        if not profiles:
            print("No profiles found inside profiles/ directory.")
        else:
            print("Available Installer Profiles:")
            for p in profiles:
                stem = p.filepath.stem if p.filepath else "default"
                print(f"  - {stem}: {p.name} ({p.description})")
        sys.exit(0)

    selected_profile: Optional[ProfileConfig] = None
    
    # 2. Match Profile
    if args.profile:
        # Check direct path first
        custom_path = Path(args.profile)
        if custom_path.exists() and custom_path.is_file():
            try:
                selected_profile = load_profile(custom_path)
            except Exception as e:
                sys.stderr.write(f"Error loading custom profile from {custom_path}: {e}\n")
                sys.exit(1)
        else:
            # Search discovered profiles
            for p in profiles:
                if p.name == args.profile or (p.filepath and p.filepath.stem == args.profile):
                    selected_profile = p
                    break
            if not selected_profile:
                sys.stderr.write(f"Error: Installer profile '{args.profile}' not found.\n")
                sys.exit(1)
    else:
        # Fall back to first profile matching 001_*.toml if it exists
        for p in profiles:
            if p.filepath and p.filepath.name.startswith("001_") and p.filepath.name.endswith(".toml"):
                selected_profile = p
                break
        
        # If no default.toml but profiles exist, select the first one
        if not selected_profile and profiles:
            selected_profile = profiles[0]

    # 3. Instantiate Tasks (Using profile config or hardcoded fallback)
    if selected_profile:
        profile_name = selected_profile.name
        raw_sequence = selected_profile.phase1_tasks if phase1 else selected_profile.phase2_tasks
        
        # Convert parsed tasks to OrchestratorTask instances with correct path resolution
        tasks: List[OrchestratorTask] = []
        for i, t in enumerate(raw_sequence):
            resolved_path = SCRIPT_DIR / t.script_name
            if not resolved_path.exists():
                resolved_path = None
                
            interpreter = t.interpreter
            if resolved_path:
                interpreter = resolve_interpreter(resolved_path)
                
            state_key = hashlib.md5(f"{i}:{t.script_name}:{'-'.join(t.args)}".encode()).hexdigest()
            
            is_interactive = t.interactive
            if resolved_path and is_script_interactive(resolved_path):
                is_interactive = True

            tasks.append(OrchestratorTask(
                script_name=t.script_name,
                args=t.args,
                ignore_fail=t.ignore_fail,
                interactive=is_interactive,
                interpreter=interpreter,
                state_key=state_key,
                resolved_path=resolved_path
            ))
    else:
        # Hardcoded fallback sequence
        profile_name = "Hardcoded Default"
        sequence = FALLBACK_ISO_SEQUENCE if phase1 else FALLBACK_CHROOT_SEQUENCE
        tasks: List[OrchestratorTask] = []
        for i, (name, s_args, ignore, interactive) in enumerate(sequence):
            resolved_path = SCRIPT_DIR / name
            if not resolved_path.exists():
                resolved_path = None
                
            interpreter = "bash"
            if resolved_path:
                interpreter = resolve_interpreter(resolved_path)
                
            state_key = hashlib.md5(f"{i}:{name}:{'-'.join(s_args)}".encode()).hexdigest()
            
            is_interactive = interactive
            if resolved_path and is_script_interactive(resolved_path):
                is_interactive = True

            tasks.append(OrchestratorTask(
                script_name=name,
                args=s_args,
                ignore_fail=ignore,
                interactive=is_interactive,
                interpreter=interpreter,
                state_key=state_key,
                resolved_path=resolved_path
            ))
            
    if phase2:
        phase_title = "PHASE 2: CHROOT"
        state_file = Path("/root/.arch_install_phase2.state")
        lock_file = Path("/tmp/orchestrator_phase2.lock")
    else:
        phase_title = "PHASE 1: ISO"
        state_file = Path("/tmp/.arch_install_phase1.state")
        lock_file = Path("/tmp/orchestrator_phase1.lock")
        
    if args.dry_run:
        print(f"=== DRY RUN FOR {phase_title} ===")
        print(f"Active Profile: {profile_name}")
        print(f"State file: {state_file}")
        for i, t in enumerate(tasks):
            status = "PENDING"
            if not t.resolved_path:
                status = "MISSING"
            print(f"  {i+1:2d}. {t.script_name} {' '.join(t.args)} [{'IGNORE_FAIL' if t.ignore_fail else 'STRICT'}] [{'INTERACTIVE' if t.interactive else 'NON-INT'}] -> {status} (using {t.interpreter})")
        sys.exit(0)
        
    if args.reset:
        if state_file.exists():
            try:
                state_file.unlink()
                print(f"Reset completion state for {phase_title}")
            except Exception as e:
                sys.stderr.write(f"Failed to reset state: {e}\n")
        else:
            print(f"No state file found for {phase_title}")
        sys.exit(0)
        
    if not acquire_lock(lock_file):
        sys.exit(1)
        
    if os.geteuid() != 0:
        sys.stderr.write("Error: This installer orchestrator must be run as root.\n")
        sys.exit(1)
        
    missing = [t.script_name for t in tasks if not t.resolved_path]
    if missing:
        sys.stderr.write(f"Error: Missing critical script files in {SCRIPT_DIR}:\n")
        for m in missing:
            sys.stderr.write(f"  - {m}\n")
        sys.exit(1)
        
    app = DuskyOrchestratorApp(
        tasks=tasks,
        phase_title=phase_title,
        profile_name=profile_name,
        state_file=state_file,
        manual=args.manual,
        stop_on_fail=args.stop_on_fail,
        force=args.force
    )
    app.run()
