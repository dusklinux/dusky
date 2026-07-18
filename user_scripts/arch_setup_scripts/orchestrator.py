#!/usr/bin/env python3
# ==============================================================================
#  ARCH LINUX MASTER ORCHESTRATOR
# ==============================================================================
# A Textual-based UI for executing a sequenced series of bash/python scripts
# to install dotfiles and configure an Arch Linux system.
#
# Profiles are stored in the profiles/ directory as TOML files.
# ==============================================================================

import os
import sys
import subprocess
import time
import fcntl
import hashlib
import shlex
import tomllib
import argparse
from pathlib import Path
from dataclasses import dataclass, field
from typing import List, Dict, Optional, Tuple

from rich.console import Console
from rich.text import Text

from textual.app import App, ComposeResult
from textual.containers import Container, Horizontal, Vertical
from textual.widgets import Header, Footer, Static, RichLog, ProgressBar, OptionList
from textual.widgets.option_list import Option
from textual import work
from textual.reactive import reactive

# ==============================================================================
#  CONSTANTS & PATHS
# ==============================================================================
SCRIPT_DIR = Path(__file__).resolve().parent
PROFILES_DIR = SCRIPT_DIR / "profiles"
LOG_BASE_DIR = Path.home() / "Documents" / "logs"
STATE_BASE_DIR = Path.home() / "Documents"
LOCK_FILE = Path(f"/run/user/{os.getuid()}/dusky-orchestra.lock")

# We use the same theme as update_dusky for consistency
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
    raw_entry: str
    mode: str          # 'U' or 'S'
    script_name: str
    args: List[str]
    ignore_fail: bool
    interactive: bool = False  # Added
    
    # Resolved at runtime
    resolved_path: Optional[Path] = None
    interpreter: str = "bash"
    state_key: str = ""

@dataclass
class ProfileConfig:
    filepath: Path
    name: str
    description: str
    post_script_delay: int
    
    # Git
    git_enabled: bool
    git_dir: str
    git_work_tree: str
    git_remote: str
    
    # Paths
    search_dirs: List[str]
    conflict_resolutions: Dict[str, str]
    
    # Sequences
    tasks: List[OrchestratorTask]

# ==============================================================================
#  CLI ARGUMENTS & STATE
# ==============================================================================
def parse_args():
    parser = argparse.ArgumentParser(description="Dusky Arch Linux Orchestrator")
    parser.add_argument("--profile", type=str, help="Execute specific profile (name or filename stem)")
    parser.add_argument("--list", action="store_true", help="List all available profiles and exit")
    parser.add_argument("--list-scripts", action="store_true", help="List sequence of selected profile and exit")
    parser.add_argument("--reset", action="store_true", help="Reset the state file for the selected profile")
    parser.add_argument("--dry-run", action="store_true", help="Validate everything but do not execute any scripts")
    parser.add_argument("--force", action="store_true", help="Pass --force to all executed scripts (handled by subscripts natively usually)")
    parser.add_argument("--manual", "-m", action="store_true", help="Prompt before executing every single script")
    parser.add_argument("--stop-on-fail", action="store_true", help="Halt execution immediately if a script fails (default: prompts user)")
    return parser.parse_args()

# ==============================================================================
#  UTILITIES & LOCKING
# ==============================================================================
def resolve_home(path_str: str) -> Path:
    if path_str.startswith("~/"):
        return Path.home() / path_str[2:]
    elif path_str.startswith("${HOME}/"):
        return Path.home() / path_str[8:]
    return Path(path_str)

def get_lock_holders() -> str:
    """Finds PIDs holding our lock file descriptor via /proc."""
    if not LOCK_FILE.exists():
        return ""
    try:
        real_lock = LOCK_FILE.resolve()
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

def acquire_lock() -> bool:
    """Acquires a non-blocking lock. Identical logic to update_dusky."""
    LOCK_FILE.parent.mkdir(parents=True, exist_ok=True)
    try:
        fd = os.open(str(LOCK_FILE), os.O_CREAT | os.O_RDWR, 0o600)
    except Exception as e:
        sys.stderr.write(f"\033[1;31m[ERROR]\033[0m Could not open lock file {LOCK_FILE}: {e}\n")
        return False

    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        return True
    except BlockingIOError:
        sys.stdout.write(f"\033[1;31m[ERROR]\033[0m Another instance is already running.\n")
        holders = get_lock_holders()
        if holders:
            sys.stdout.write(f"{holders}\n")
        else:
            sys.stdout.write("\033[1;33m[WARN]\033[0m No live lock holder identified. Stale lock?\n")
            try:
                # If no one holds it in /proc, try a blocking wait for 2 seconds
                # to clear any fleeting race conditions.
                sys.stdout.write("Attempting to acquire stale lock...\n")
                fcntl.flock(fd, fcntl.LOCK_EX)
                return True
            except Exception:
                sys.stdout.write("\033[1;31m[ERROR]\033[0m Failed to acquire lock.\n")
                return False
        return False

# ==============================================================================
#  SUDO VALIDATION
# ==============================================================================
def verify_sudo() -> bool:
    if not shutil.which("sudo"):
        sys.stdout.write("\033[1;31m[FATAL]\033[0m sudo is required but not installed.\n")
        return False

    sys.stdout.write("\033[1;36m[DUSKY PRE-FLIGHT]\033[0m Securing administrative privileges...\n")
    try:
        if subprocess.run(['sudo', '-n', 'true'], capture_output=True).returncode == 0:
            return True
    except Exception:
        pass

    if sys.stdin.isatty():
        try:
            subprocess.run(['sudo', '-v'], check=True)
            return True
        except subprocess.CalledProcessError:
            pass

    sys.stdout.write("\033[1;31m[FATAL]\033[0m Sudo authentication failed. Aborting.\n")
    return False

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
        raw_entry=raw_entry,
        mode=mode,
        script_name=cmd_tokens[0],
        args=cmd_tokens[1:],
        ignore_fail=ignore_fail,
        interactive=interactive
    )

def load_profile(filepath: Path) -> ProfileConfig:
    with open(filepath, "rb") as f:
        data = tomllib.load(f)

    p_data = data.get("profile", {})
    g_data = data.get("git", {})
    s_data = data.get("search_dirs", {})
    c_data = data.get("conflict_resolutions", {})
    seq_data = data.get("sequence", {})

    tasks = []
    for line in seq_data.get("scripts", []):
        try:
            tasks.append(parse_task_entry(line))
        except ValueError as e:
            sys.stderr.write(f"Error parsing profile {filepath.name}: {e}\n")
            sys.exit(1)

    return ProfileConfig(
        filepath=filepath,
        name=p_data.get("name", filepath.stem),
        description=p_data.get("description", ""),
        post_script_delay=p_data.get("post_script_delay", 0),
        git_enabled=g_data.get("enabled", False),
        git_dir=g_data.get("git_dir", "~/dusky"),
        git_work_tree=g_data.get("work_tree", "~/"),
        git_remote=g_data.get("remote", "origin"),
        search_dirs=[str(resolve_home(d)) for d in s_data.get("dirs", [])],
        conflict_resolutions=c_data,
        tasks=tasks
    )

def discover_profiles() -> List[ProfileConfig]:
    if not PROFILES_DIR.exists():
        sys.stderr.write(f"\033[1;31m[FATAL]\033[0m Profiles directory missing: {PROFILES_DIR}\n")
        sys.exit(1)

    profiles = []
    for f in sorted(PROFILES_DIR.glob("*.toml")):
        profiles.append(load_profile(f))
    return profiles

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
#  PRE-FLIGHT RESOLUTION
# ==============================================================================
def resolve_and_validate_manifest(profile: ProfileConfig) -> bool:
    success = True
    for i, task in enumerate(profile.tasks):
        # 1. State Key
        task.state_key = hashlib.md5(f"{i}:{task.script_name}:{'-'.join(task.args)}".encode()).hexdigest()

        # 2. Resolution
        if "/" in task.script_name:
            if task.script_name.startswith("~/") or task.script_name.startswith("${HOME}/"):
                cand = resolve_home(task.script_name)
            elif task.script_name.startswith("/"):
                cand = Path(task.script_name)
            else:
                cand = Path.home() / task.script_name
                
            if cand.exists() and cand.is_file():
                task.resolved_path = cand
        else:
            if task.script_name in profile.conflict_resolutions:
                cand = resolve_home(profile.conflict_resolutions[task.script_name])
                if cand.exists() and cand.is_file():
                    task.resolved_path = cand
            else:
                matches = []
                for d in profile.search_dirs:
                    cand = Path(d) / task.script_name
                    if cand.exists() and cand.is_file():
                        matches.append(cand)
                if len(matches) == 1:
                    task.resolved_path = matches[0]
                elif len(matches) > 1:
                    sys.stderr.write(f"\033[1;31m[CONFLICT]\033[0m Multiple versions of {task.script_name} found:\n")
                    for m in matches: sys.stderr.write(f"  - {m}\n")
                    success = False

        if not task.resolved_path:
            sys.stderr.write(f"\033[1;31m[MISSING]\033[0m Could not find {task.script_name} in search dirs.\n")
            success = False
            continue

        # Auto-detect interactive comment header
        if is_script_interactive(task.resolved_path):
            task.interactive = True

        # 3. Interpreter mapping
        with open(task.resolved_path, 'r', errors='ignore') as f:
            first_line = f.readline().strip()
        
        has_py_ext = task.resolved_path.suffix == '.py'
        has_sh_ext = task.resolved_path.suffix == '.sh'
        has_py_shebang = "python" in first_line
        has_sh_shebang = any(x in first_line for x in ["bash", "sh", "zsh"])
        
        if (has_py_ext and has_sh_shebang) or (has_sh_ext and has_py_shebang):
            sys.stderr.write(f"\033[1;33m[CONTRADICTION]\033[0m {task.script_name} has conflicting shebang and extension.\n")
            success = False
            
        if has_py_ext or has_py_shebang:
            task.interpreter = "python"
        elif has_sh_ext or has_sh_shebang:
            task.interpreter = "bash"

    return success

# ==============================================================================
#  GIT SELF-UPDATE ENGINE
# ==============================================================================
def run_git_self_update(profile: ProfileConfig) -> bool:
    """Updates the orchestrator script itself from Git if changes are detected."""
    if not profile.git_enabled:
        return False
        
    git_dir = resolve_home(profile.git_dir)
    work_tree = resolve_home(profile.git_work_tree)
    
    if not git_dir.exists():
        sys.stdout.write(f"\033[1;33m[WARN]\033[0m Git dir not found ({git_dir}). Skipping self-update.\n")
        return False
        
    base_cmd = ["git", f"--git-dir={git_dir}", f"--work-tree={work_tree}"]
    sys.stdout.write("\033[1;36m[GIT]\033[0m Fetching upstream updates...\n")
    
    try:
        subprocess.run(base_cmd + ["fetch", profile.git_remote], check=True, capture_output=True)
    except subprocess.CalledProcessError:
        sys.stdout.write("\033[1;33m[WARN]\033[0m Git fetch failed (offline?). Proceeding without update.\n")
        return False

    try:
        # Check if HEAD differs from upstream HEAD
        local_head = subprocess.check_output(base_cmd + ["rev-parse", "HEAD"]).decode().strip()
        remote_head = subprocess.check_output(base_cmd + ["rev-parse", f"{profile.git_remote}/HEAD"]).decode().strip()
        
        if local_head == remote_head:
            sys.stdout.write("\033[1;32m[GIT]\033[0m Orchestrator is up to date.\n")
            return False
            
        # We are diverged/behind. We need to reset hard.
        # But wait! We need to know if *this exact file* (orchestrator.py) changes!
        my_path = Path(__file__).resolve()
        my_rel = my_path.relative_to(work_tree)
        
        # Hash before
        h_before = hashlib.sha256(my_path.read_bytes()).hexdigest()
        
        sys.stdout.write("\033[1;36m[GIT]\033[0m Applying updates via hard reset...\n")
        subprocess.run(base_cmd + ["reset", "--hard", f"{profile.git_remote}/HEAD"], check=True, capture_output=True)
        
        # Hash after
        h_after = hashlib.sha256(my_path.read_bytes()).hexdigest()
        
        if h_before != h_after:
            sys.stdout.write("\033[1;33m[GIT]\033[0m Orchestrator script updated! Restarting process...\n")
            # Exec replaces the current process natively
            os.execv(sys.executable, [sys.executable] + sys.argv)
            
        return True # We did update, but not ourselves
    except Exception as e:
        sys.stdout.write(f"\033[1;31m[ERROR]\033[0m Git self-update failed: {e}\n")
        return False

# ==============================================================================
#  TEXTUAL SELECTOR APP
# ==============================================================================
class ProfileSelectorApp(App):
    CSS = """
    Screen {
        align: center middle;
    }
    #selector_container {
        width: 80;
        height: auto;
        border: heavy $accent;
        background: $surface;
        padding: 1 2;
    }
    #title {
        text-align: center;
        text-style: bold;
        color: $accent;
        margin-bottom: 1;
    }
    OptionList {
        height: auto;
        border: none;
    }
    """

    def __init__(self, profiles: List[ProfileConfig]):
        super().__init__()
        self.profiles = profiles
        self.selected_profile: Optional[ProfileConfig] = None

    def compose(self) -> ComposeResult:
        with Container(id="selector_container"):
            yield Static("◈ DUSKY ARCH ORCHESTRATOR", id="title")
            options = []
            for i, p in enumerate(self.profiles):
                # Highlight default (first item)
                prefix = "❯ " if i == 0 else "  "
                options.append(Option(f"{prefix}{i+1}. {p.name:<25} {p.description}", id=str(i)))
            yield OptionList(*options, id="profiles_list")
            yield Static("Press Enter to select. Esc to quit.", classes="help_text")

    def on_option_list_option_selected(self, event: OptionList.OptionSelected) -> None:
        self.selected_profile = self.profiles[int(event.option_id)]
        self.exit(0)
        
    def on_key(self, event) -> None:
        if event.key == "escape":
            self.exit(1)

# ==============================================================================
#  TEXTUAL EXECUTION APP
# ==============================================================================
import shutil
import pty
import termios
import struct
import select

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

    # Bindings removed to prevent conflict with PTY input; handled dynamically in on_key

    def __init__(self, profile: ProfileConfig, has_sudo: bool, manual: bool, stop_on_fail: bool, force: bool):
        super().__init__()
        self.profile = profile
        self.tasks = profile.tasks
        self.has_sudo = has_sudo
        self.manual = manual
        self.stop_on_fail = stop_on_fail
        self.force_flag = force
        self.current_idx = 0
        self.completed_keys = set()
        self.current_pty_master = None
        
        self.state_file = STATE_BASE_DIR / f".install_state_{self.profile.name.replace(' ', '_')}"
        if self.state_file.exists():
            self.completed_keys = set(self.state_file.read_text().splitlines())

        self.log_widget = RichLog(id="syslog", highlight=True, markup=True)
        self.left_pane = Vertical(id="left_pane")
        self.progress_bar = ProgressBar(total=len(self.tasks), show_eta=False, id="progress_bar")

        # UI State
        self.waiting_for_input = False
        self.input_action = None # 'confirm', 'retry_skip'

    def compose(self) -> ComposeResult:
        with Horizontal(id="header"):
            yield Static(f"◈ DUSKY ORCHESTRATOR  [{self.profile.name}]", classes="title")
            yield self.progress_bar
            
        with Horizontal(id="main_content"):
            yield self.left_pane
            with Vertical(id="right_pane"):
                yield self.log_widget
                
        yield Footer()

    def on_mount(self) -> None:
        self.render_task_list()
        
        # Advance completed count
        for t in self.tasks:
            if t.state_key in self.completed_keys:
                self.progress_bar.advance(1)
                
        self.log_system(f"Loaded Profile: {self.profile.name} ({len(self.tasks)} scripts)")
        self.log_system(f"Search Dirs: {len(self.profile.search_dirs)}")
        self.run_execution_loop()

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
                icon = f"[{THEME['error']}]x[/]" # Failed
            else:
                icon = f"[{THEME['pending']}]·[/]"
                
            name = t.script_name[:25]
            row = Horizontal(
                Static(icon, classes="task_icon"),
                Static(t.mode, classes="task_mode"),
                Static(name, classes="task_name"),
                classes="task_row",
                id=f"row_{i}"
            )
            self.left_pane.mount(row)

    def update_task_icon(self, idx: int, status: str):
        row = self.query_one(f"#row_{idx}")
        icon_w = row.children[0]
        if status == "running":
            icon_w.update(f"[{THEME['info']}]◉[/]")
        elif status == "done":
            icon_w.update(f"[{THEME['done']}]✓[/]")
        elif status == "failed":
            icon_w.update(f"[{THEME['error']}]x[/]")

    def log_system(self, msg: str):
        self.log_widget.write(f"[{THEME['info']}][SYSTEM][/] {msg}")

    def log_task(self, msg: str, is_err: bool = False):
        color = THEME['error'] if is_err else THEME['run']
        self.log_widget.write(f"[{color}]{msg}[/]")

    def log_header(self, task: OrchestratorTask):
        self.log_widget.write(f"\n[bold {THEME['info']}]>>> PROCESS INITIATED: {task.script_name}[/]")

    @work(thread=True)
    def run_execution_loop(self):
        while self.current_idx < len(self.tasks):
            task = self.tasks[self.current_idx]
            
            if task.state_key in self.completed_keys:
                self.current_idx += 1
                continue
                
            if not task.resolved_path:
                self.app.call_from_thread(self.handle_missing_task, task)
                return # Thread suspended until UI action

            if self.manual:
                self.app.call_from_thread(self.prompt_manual_continue, task)
                return # Thread suspended
                
            self.execute_current_task()
            return # Exit loop to avoid recursion, execute_current_task chains back

        # Done
        self.app.call_from_thread(self.log_system, "All tasks completed successfully!")
        time.sleep(2)
        self.app.call_from_thread(self.exit, 0)

    def handle_missing_task(self, task: OrchestratorTask):
        self.update_task_icon(self.current_idx, "failed")
        self.log_task(f"Missing file: {task.script_name}", True)
        self.prompt_retry_skip("File missing. Skip [s] or Quit [q]?")

    def prompt_manual_continue(self, task: OrchestratorTask):
        self.update_task_icon(self.current_idx, "running")
        self.log_header(task)
        self.log_system("Manual mode: Proceed [y] / Skip [s] / Quit [q]?")
        self.waiting_for_input = True
        self.input_action = 'manual'

    def prompt_retry_skip(self, msg: str):
        self.log_system(msg)
        self.waiting_for_input = True
        self.input_action = 'retry_skip'

    @work(thread=True)
    def execute_current_task(self):
        task = self.tasks[self.current_idx]
        self.app.call_from_thread(self.update_task_icon, self.current_idx, "running")
        self.app.call_from_thread(self.log_header, task)
        
        args = list(task.args)
        if self.force_flag and "--force" not in args:
            args.append("--force")
            
        cmd = [task.interpreter, str(task.resolved_path)] + args
        if task.mode == "S":
            cmd = ["sudo"] + cmd

        if task.interactive:
            self.app.call_from_thread(self.log_system, "Suspending UI for interactive script...")
            time.sleep(0.5)
            with self.suspend():
                rc = subprocess.run(cmd).returncode
            self.app.call_from_thread(self.log_system, f"UI Resumed. Script exited with: {rc}")
            
            if rc == 0:
                self.app.call_from_thread(self.task_success, task)
            else:
                if task.ignore_fail:
                    self.app.call_from_thread(self.log_system, f"Task failed with {rc} but marked ignore-fail. Continuing.")
                    self.app.call_from_thread(self.task_success, task)
                else:
                    self.app.call_from_thread(self.task_failure, task, rc)
            return
            
        try:
            # We use a PTY so the underlying scripts think they are interactive
            master, slave = pty.openpty()
            self.current_pty_master = master
            proc = subprocess.Popen(
                cmd,
                stdin=slave,
                stdout=slave,
                stderr=slave,
                close_fds=True,
                text=True
            )
            os.close(slave)
            
            output_buffer = ""
            while proc.poll() is None:
                r, _, _ = select.select([master], [], [], 0.1)
                if r:
                    try:
                        data = os.read(master, 1024).decode(errors='replace')
                        if data:
                            # Flush chunks immediately to prevent prompt hangs
                            clean = data.replace('\x1b[0m', '').strip('\r')
                            if clean:
                                self.app.call_from_thread(self.log_task, clean)
                    except OSError:
                        break
            
            # Flush rest
            try:
                data = os.read(master, 1024).decode(errors='replace')
                if data:
                    clean = data.replace('\x1b[0m', '').strip('\r')
                    if clean:
                        self.app.call_from_thread(self.log_task, clean)
            except OSError:
                pass
                
            self.current_pty_master = None
            os.close(master)
            rc = proc.wait()
            
            if rc == 0:
                self.app.call_from_thread(self.task_success, task)
            else:
                if task.ignore_fail:
                    self.app.call_from_thread(self.log_system, f"Task failed with {rc} but marked ignore-fail. Continuing.")
                    self.app.call_from_thread(self.task_success, task)
                else:
                    self.app.call_from_thread(self.task_failure, task, rc)
                    
        except Exception as e:
            self.app.call_from_thread(self.task_failure, task, str(e))

    def task_success(self, task: OrchestratorTask):
        self.update_task_icon(self.current_idx, "done")
        self.log_task(">>> EXECUTION SUCCESSFUL")
        self.completed_keys.add(task.state_key)
        
        # Save state atomically
        with open(self.state_file, "a") as f:
            f.write(task.state_key + "\n")
            
        self.progress_bar.advance(1)
        if self.profile.post_script_delay > 0:
            time.sleep(self.profile.post_script_delay)
            
        self.current_idx += 1
        self.run_execution_loop()

    def task_failure(self, task: OrchestratorTask, reason):
        self.update_task_icon(self.current_idx, "failed")
        self.log_task(f">>> EXECUTION FAILED: {reason}", True)
        if self.stop_on_fail:
            self.log_system("stop-on-fail active. Aborting.")
            time.sleep(2)
            self.exit(1)
        else:
            self.prompt_retry_skip("Task Failed. Retry [r] / Skip [s] / Quit [q]?")

    def action_quit_app(self):
        self.exit(1)
        
    def action_skip_task(self):
        if self.waiting_for_input and self.input_action in ('manual', 'retry_skip'):
            self.waiting_for_input = False
            self.log_system("Skipping task.")
            self.update_task_icon(self.current_idx, "failed") # Leave marked as not done
            self.current_idx += 1
            self.progress_bar.advance(1)
            self.run_execution_loop()

    def action_retry_task(self):
        if self.waiting_for_input and self.input_action == 'retry_skip':
            self.waiting_for_input = False
            self.log_system("Retrying task...")
            self.run_execution_loop() # Retries current_idx
            
    def action_yes(self):
        if self.waiting_for_input and self.input_action == 'manual':
            self.waiting_for_input = False
            self.execute_current_task()

    def action_no(self):
        if self.waiting_for_input and self.input_action in ('manual', 'retry_skip'):
            self.waiting_for_input = False
            self.log_system("Aborting.")
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
            return
            
        if self.current_pty_master is not None:
            try:
                if event.is_printable:
                    os.write(self.current_pty_master, event.character.encode())
                elif event.key == "enter":
                    os.write(self.current_pty_master, b"\r")
                elif event.key == "backspace":
                    os.write(self.current_pty_master, b"\x08")
            except OSError:
                pass

# ==============================================================================
#  MAIN ENTRY
# ==============================================================================
if __name__ == "__main__":
    args = parse_args()
    
    profiles = discover_profiles()
    if not profiles:
        sys.stderr.write("No profiles found.\n")
        sys.exit(1)
        
    selected_profile = None
    
    if args.list:
        for p in profiles:
            print(f"- {p.filepath.stem}: {p.name} ({p.description})")
        sys.exit(0)
        
    if args.profile:
        # Match by name or filename
        for p in profiles:
            if p.name == args.profile or p.filepath.stem == args.profile:
                selected_profile = p
                break
        if not selected_profile:
            sys.stderr.write(f"Profile '{args.profile}' not found.\n")
            sys.exit(1)
    else:
        # Run selector UI
        selector = ProfileSelectorApp(profiles)
        selector.run()
        selected_profile = selector.selected_profile
        
    if not selected_profile:
        sys.exit(1)
        
    if args.reset:
        sf = STATE_BASE_DIR / f".install_state_{selected_profile.name.replace(' ', '_')}"
        if sf.exists():
            sf.unlink()
            print(f"Reset state for {selected_profile.name}")
        sys.exit(0)
        
    if args.list_scripts:
        print(f"Sequence for {selected_profile.name}:")
        for i, t in enumerate(selected_profile.tasks):
            print(f"{i+1:3d}. [{t.mode}] {t.script_name} {' '.join(t.args)}")
        sys.exit(0)

    # 1. Self Update Check
    if not args.dry_run and run_git_self_update(selected_profile):
        sys.exit(0) # In theory execv handled it, but safety exit
        
    # 2. Lock
    if not acquire_lock():
        sys.exit(1)
        
    # 3. Resolve & Validate (dry-run stops here)
    if not resolve_and_validate_manifest(selected_profile):
        sys.stderr.write("Validation failed.\n")
        sys.exit(1)
        
    if args.dry_run:
        print("Dry-run complete. Everything is valid.")
        sys.exit(0)
        
    # 4. Sudo Validation
    has_sudo = False
    if any(t.mode == 'S' for t in selected_profile.tasks):
        if not verify_sudo():
            sys.exit(1)
        has_sudo = True
        
    # 5. UI Exec
    app = DuskyOrchestratorApp(
        profile=selected_profile,
        has_sudo=has_sudo,
        manual=args.manual,
        stop_on_fail=args.stop_on_fail,
        force=args.force
    )
    app.run()

