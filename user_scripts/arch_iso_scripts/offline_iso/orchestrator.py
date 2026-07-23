#!/usr/bin/env python3
# DUSKY_BOOTSTRAP_PACKAGES: python python-textual python-rich git
# dusky_interactive=true
# ==============================================================================
#  ARCH LINUX ISO TEXTUAL ORCHESTRATOR (v19.0 - Async PTY Engine + Auto-Prompt)
# ==============================================================================
# Architecture: Asynchronous Non-Blocking PTY Stream Engine | Textual Split TUI
# Features: Progress Bar/Speed Extraction | Auto-Prompt Responder | State Persistence
# Compatibility: Python 3.14+ | Textual 8.2+ | Arch Linux ISO (2026+)
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
import pty
import termios
import struct
import re
import tomllib
import atexit
from pathlib import Path
from dataclasses import dataclass
from enum import Enum, auto
from typing import List, Dict, Optional, Tuple

from rich.console import Console
from rich.text import Text

from textual.app import App, ComposeResult
from textual.containers import Container, Horizontal, Vertical
from textual.widgets import Header, Footer, Static, RichLog, ProgressBar, Button, Label, Input, OptionList
from textual.widgets.option_list import Option
from textual.screen import ModalScreen
from textual import work, on

# ==============================================================================
# CONSTANTS & PATHS
# ==============================================================================
SCRIPT_DIR = Path(__file__).resolve().parent
PROFILES_DIR = SCRIPT_DIR / "profiles"

# High-Performance Regexes
ANSI_STRIP_REGEX = re.compile(
    r'\x1B(?:[@-Z\\-_]|\[(?>(?:[0-?]*+)[ -/]*+[@-~])|\](?>\d*;.*?)(?:\x07|\x1B\\)|\]8;;.*?(?:\x07|\x1B\\)|\x1B\(B)'
)
PCT_REGEX = re.compile(r'(?<![0-9])(?>\d{1,2}|100)%')
SPEED_ETA_REGEX = re.compile(r'(\d+(?:\.\d+)?\s+[KMG]?i?B/s)\s+([\d:]+)', re.IGNORECASE)
PROGRESS_BAR_REGEX = re.compile(r'\[[#=\- oO@%:.0123456789━─░▒▓█▏▎▍▌▋▊▉●○◉◌]{3,}\]|\b\d{1,3}%\b')
INTERACTIVE_RE = re.compile(r'^\s*#\s*dusky_interactive\s*=\s*(?:true|1)\b', re.IGNORECASE)

PROMPT_RULES: List[Tuple[str, re.Pattern, str]] = [
    ("pgp_import", re.compile(r"(?i)(::\s*Import PGP key.*\?\s*\[Y/n\]|::\s*Append key\?.*\[Y/n\]|Import PGP key.*\?\s*\[Y/n\])"), "y\n"),
    ("pacman_proceed", re.compile(r"(?i)::\s*(Proceed with (?:installation|download|upgrade)|Continue (?:installation|download|upgrade)).*\?\s*\[Y/n\]"), "y\n"),
    ("pacman_replace", re.compile(r"(?i)::\s*Replace\s+.*\?\s*\[Y/n\]"), "y\n"),
    ("pacman_remove_conflict", re.compile(r"(?i)::\s*Remove conflicting file.*\?\s*\[Y/n\]"), "y\n"),
    ("generic_yes", re.compile(r"(?i)\[Y/n\]|\(Y/n\)"), "y\n"),
]

_LOCK_FD: Optional[int] = None

# ==============================================================================
# DATA CLASSES
# ==============================================================================
class TaskStatus(Enum):
    PENDING = auto()
    RUNNING = auto()
    COMPLETED = auto()
    FAILED = auto()
    SKIPPED = auto()

@dataclass
class OrchestratorTask:
    script_name: str
    args: List[str]
    ignore_fail: bool
    interactive: bool = False
    interpreter: str = "bash"
    state_key: str = ""
    resolved_path: Optional[Path] = None
    status: TaskStatus = TaskStatus.PENDING

@dataclass
class ProfileConfig:
    filepath: Optional[Path]
    name: str
    description: str
    phase1_tasks: List[OrchestratorTask]
    phase2_tasks: List[OrchestratorTask]

# ==============================================================================
# PROFILE PARSER & ENGINE
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
        if flag in ("true", "ignore", "ignore-fail"):
            ignore_fail = True
        elif flag in ("interactive", "tui", "prompt"):
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
# LOCKING & INTERPRETER RESOLUTION
# ==============================================================================
def parse_args():
    parser = argparse.ArgumentParser(description="Dusky Arch ISO Textual Orchestrator")
    parser.add_argument("--phase1", action="store_true", help="Run Phase 1 (ISO Environment)")
    parser.add_argument("--phase2", action="store_true", help="Run Phase 2 (Chroot Environment)")
    parser.add_argument("--reset", action="store_true", help="Reset execution state for the current phase")
    parser.add_argument("--dry-run", action="store_true", help="Dry run: validate scripts presence and exit")
    parser.add_argument("--force", action="store_true", help="Pass --force flag to subscripts")
    parser.add_argument("--manual", "-m", action="store_true", help="Manual mode: prompt before each script")
    parser.add_argument("--stop-on-fail", action="store_true", help="Halt execution if any script fails")
    parser.add_argument("--profile", type=str, help="Specify profile TOML to execute")
    parser.add_argument("--list-profiles", action="store_true", help="List all available installer profiles and exit")
    return parser.parse_args()

def _cleanup_lock(lock_file: Path):
    global _LOCK_FD
    if _LOCK_FD is not None:
        try: fcntl.flock(_LOCK_FD, fcntl.LOCK_UN)
        except OSError: pass
        try: os.close(_LOCK_FD)
        except OSError: pass
        _LOCK_FD = None
    try: lock_file.unlink(missing_ok=True)
    except OSError: pass

def acquire_lock(lock_file: Path) -> bool:
    global _LOCK_FD
    lock_file.parent.mkdir(parents=True, exist_ok=True)
    try:
        fd = os.open(str(lock_file), os.O_CREAT | os.O_RDWR | os.O_CLOEXEC, 0o600)
    except Exception as e:
        sys.stderr.write(f"\033[1;31m[ERROR]\033[0m Could not open lock file {lock_file}: {e}\n")
        return False

    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        _LOCK_FD = fd
        atexit.register(lambda: _cleanup_lock(lock_file))
        return True
    except BlockingIOError:
        sys.stderr.write(f"\033[1;31m[ERROR]\033[0m Another instance is already running on {lock_file}.\n")
        try: os.close(fd)
        except OSError: pass
        return False

def resolve_interpreter(script_path: Path) -> Tuple[str, bool]:
    is_interactive = False
    first_line = ""
    try:
        with open(script_path, 'r', encoding='utf-8', errors='ignore') as f:
            for line_num in range(20):
                line = f.readline()
                if not line: break
                if line_num == 0: first_line = line.strip()
                if INTERACTIVE_RE.search(line): is_interactive = True
    except Exception:
        pass

    if "python" in first_line or script_path.suffix == '.py':
        return "python3", is_interactive
    return "bash", is_interactive

# ==============================================================================
# MODAL SCREENS
# ==============================================================================
class FailureModalScreen(ModalScreen):
    def __init__(self, task_name: str, error_msg: str):
        super().__init__()
        self.task_name = task_name
        self.error_msg = error_msg

    def compose(self) -> ComposeResult:
        with Container(id="modal_dialog"):
            yield Label(f"⚠ TASK FAILED: {self.task_name}", id="modal_title")
            yield Static(self.error_msg, id="error_details")
            with Horizontal(id="button_bar"):
                yield Button("Retry [R]", variant="primary", id="btn_retry")
                yield Button("Skip [S]", variant="warning", id="btn_skip")
                yield Button("Quit [Q]", variant="error", id="btn_quit")

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "btn_retry":
            self.dismiss("retry")
        elif event.button.id == "btn_skip":
            self.dismiss("skip")
        elif event.button.id == "btn_quit":
            self.dismiss("quit")

    def on_key(self, event) -> None:
        key = event.key.lower()
        if key == "r":
            self.dismiss("retry")
        elif key == "s":
            self.dismiss("skip")
        elif key == "q":
            self.dismiss("quit")

class ManualModalScreen(ModalScreen):
    def __init__(self, task_name: str):
        super().__init__()
        self.task_name = task_name

    def compose(self) -> ComposeResult:
        with Container(id="manual_dialog"):
            yield Label(f"◈ MANUAL STEP REQUIRED", id="manual_title")
            yield Static(f"About to execute: [bold white]{self.task_name}[/bold white]\nProceed with execution?", id="manual_details")
            with Horizontal(id="button_bar"):
                yield Button("Proceed [Y]", variant="success", id="btn_yes")
                yield Button("Skip [S]", variant="warning", id="btn_skip")
                yield Button("Quit [Q]", variant="error", id="btn_quit")

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "btn_yes":
            self.dismiss("yes")
        elif event.button.id == "btn_skip":
            self.dismiss("skip")
        elif event.button.id == "btn_quit":
            self.dismiss("quit")

    def on_key(self, event) -> None:
        key = event.key.lower()
        if key in ("y", "enter", "space"):
            self.dismiss("yes")
        elif key == "s":
            self.dismiss("skip")
        elif key == "q":
            self.dismiss("quit")

# ==============================================================================
# MAIN TEXTUAL APP
# ==============================================================================
class DuskyOrchestratorApp(App):
    ENABLE_COMMAND_PALETTE = False

    CSS = """
    Screen {
        background: #0d1117;
        color: #c9d1d9;
        layout: vertical;
    }
    #top_header {
        height: 3;
        dock: top;
        background: #161b22;
        color: #58a6ff;
        padding: 0 1;
        layout: vertical;
        border-bottom: solid #30363d;
    }
    #header_title {
        text-style: bold;
        color: #58a6ff;
        width: 100%;
        text-align: center;
    }
    #header_telemetry {
        color: #e3b341;
        text-style: italic;
    }
    #progress_bar {
        margin: 0 1;
        width: 100%;
    }
    #main_content {
        layout: horizontal;
        height: 1fr;
    }
    #left_pane {
        width: 38%;
        border-right: solid #30363d;
        padding: 0 1;
        height: 100%;
        overflow-y: auto;
        background: #0d1117;
    }
    #right_pane {
        width: 62%;
        height: 100%;
        padding: 0 1;
        background: #161b22;
    }
    .task_row {
        layout: horizontal;
        height: 1;
    }
    .task_icon { width: 3; text-align: center; }
    .task_mode { width: 5; text-align: center; color: #d29922; }
    .task_name { width: 1fr; color: #c9d1d9; }
    
    RichLog {
        height: 100%;
        border: none;
        background: #161b22;
        color: #c9d1d9;
        scrollbar-gutter: stable;
    }
    #footer {
        dock: bottom;
        height: 1;
        background: #090d16;
        color: #8b949e;
    }

    FailureModalScreen, ManualModalScreen {
        align: center middle;
        background: rgba(0,0,0,0.85);
    }
    #modal_dialog {
        width: 75;
        height: auto;
        border: heavy #f85149;
        background: #161b22;
        padding: 1 2;
    }
    #manual_dialog {
        width: 75;
        height: auto;
        border: heavy #58a6ff;
        background: #161b22;
        padding: 1 2;
    }
    #modal_title {
        text-align: center;
        text-style: bold;
        color: #f85149;
        margin-bottom: 1;
    }
    #manual_title {
        text-align: center;
        text-style: bold;
        color: #58a6ff;
        margin-bottom: 1;
    }
    #error_details {
        color: #d29922;
        margin-bottom: 1;
        max-height: 10;
        overflow-y: auto;
    }
    #button_bar {
        layout: horizontal;
        align: center middle;
        height: 3;
    }
    Button {
        height: 1;
        min-width: 14;
        border: none;
        margin: 0 1;
    }
    """

    BINDINGS = [
        ("q", "quit_app", "Quit"),
        ("m", "toggle_manual", "Manual Mode"),
        ("r", "reset_state", "Reset State"),
    ]

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
        self.header_title = Static(f"◈ DUSKY ARCH INSTALLER  [{self.phase_title}]  (Profile: {self.profile_name})", id="header_title")
        self.header_telemetry = Static("Status: Ready | Telemetry: Idle", id="header_telemetry")

    def compose(self) -> ComposeResult:
        with Vertical(id="top_header"):
            yield self.header_title
            with Horizontal():
                yield self.header_telemetry
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
                t.status = TaskStatus.COMPLETED
                self.progress_bar.advance(1)
                
        self.log_system(f"Started Phase: {self.phase_title}")
        self.log_system(f"Active Profile: {self.profile_name}")
        self.log_system(f"Loaded Cached State: {len(self.completed_keys)} tasks completed")
        
        self.run_worker(self.run_execution_loop())

    def render_task_list(self):
        self.left_pane.remove_children()
        for i, t in enumerate(self.tasks):
            if t.status == TaskStatus.COMPLETED or t.state_key in self.completed_keys:
                icon = "[bold #3fb950]✓[/]"
            elif not t.resolved_path:
                icon = "[bold #f85149]![/]"
            elif t.status == TaskStatus.RUNNING:
                icon = "[bold #58a6ff]◉[/]"
            elif t.status == TaskStatus.FAILED:
                icon = "[bold #f85149]x[/]"
            elif t.status == TaskStatus.SKIPPED:
                icon = "[bold #d29922]⊘[/]"
            else:
                icon = "[#8b949e]·[/]"
                
            name = t.script_name[:28]
            row = Horizontal(
                Static(icon, classes="task_icon"),
                Static("ROOT", classes="task_mode"),
                Static(name, classes="task_name"),
                classes="task_row",
                id=f"row_{i}"
            )
            self.left_pane.mount(row)

    def update_task_status(self, idx: int, status: TaskStatus):
        self.tasks[idx].status = status
        try:
            row = self.query_one(f"#row_{idx}")
            icon_w = row.children[0]
            if status == TaskStatus.RUNNING:
                icon_w.update("[bold #58a6ff]◉[/]")
            elif status == TaskStatus.COMPLETED:
                icon_w.update("[bold #3fb950]✓[/]")
            elif status == TaskStatus.FAILED:
                icon_w.update("[bold #f85149]x[/]")
            elif status == TaskStatus.SKIPPED:
                icon_w.update("[bold #d29922]⊘[/]")
        except Exception:
            pass

    def log_system(self, msg: str):
        self.log_widget.write(Text.from_ansi(f"\033[1;36m[SYSTEM]\033[0m {msg}"))

    def log_task(self, msg: str):
        self.log_widget.write(Text.from_ansi(msg))

    def update_telemetry(self, status_str: str, speed_str: str = ""):
        if speed_str:
            self.header_telemetry.update(f"Status: {status_str} | Speed/ETA: {speed_str}")
        else:
            self.header_telemetry.update(f"Status: {status_str}")

    async def run_execution_loop(self):
        while self.current_idx < len(self.tasks):
            task = self.tasks[self.current_idx]
            
            if task.state_key in self.completed_keys:
                self.current_idx += 1
                continue
                
            if not task.resolved_path:
                await self.handle_missing_task(task)
                return
                
            if self.manual:
                res = await self.push_screen_wait(ManualModalScreen(task.script_name))
                if res == "yes":
                    pass
                elif res == "skip":
                    self.task_skipped(task)
                    continue
                else:
                    self.exit(1)
                    return
                
            await self.execute_task(task)
            return

        self.log_system("All tasks in this phase completed successfully!")
        self.update_telemetry("Finished Phase")
        await asyncio.sleep(1.5)
        self.exit(0)

    async def handle_missing_task(self, task: OrchestratorTask):
        self.update_task_status(self.current_idx, TaskStatus.FAILED)
        self.log_task(f"\033[1;31m[ERROR] Missing script: {task.script_name}\033[0m")
        res = await self.push_screen_wait(FailureModalScreen(task.script_name, "Script file not found on disk."))
        if res == "retry":
            self.run_worker(self.run_execution_loop())
        elif res == "skip":
            self.task_skipped(task)
        else:
            self.exit(1)

    async def execute_task(self, task: OrchestratorTask):
        self.update_task_status(self.current_idx, TaskStatus.RUNNING)
        self.log_widget.write(Text.from_ansi(f"\n\033[1;36m>>> PROCESS INITIATED: {task.script_name}\033[0m"))
        self.update_telemetry(f"Running {task.script_name}")
        
        args = list(task.args)
        if self.force_flag and "--force" not in args:
            args.append("--force")
            
        cmd = [task.interpreter, str(task.resolved_path)] + args
        
        if task.interactive:
            # INTERACTIVE SUSPENSION: Delegate terminal directly to command
            self.log_system(f"Delegating terminal to interactive process: {task.script_name}")
            await asyncio.sleep(0.3)
            with self.suspend():
                rc = subprocess.run(cmd).returncode
            self.log_system(f"TUI Resumed. Script exited with code: {rc}")
            
            if rc == 0:
                await self.task_success(task)
            else:
                await self.task_failure(task, f"Exit code {rc}")
        else:
            # NON-INTERACTIVE PTY EXECUTION
            master_fd, slave_fd = pty.openpty()
            try:
                fl = fcntl.fcntl(master_fd, fcntl.F_GETFL)
                fcntl.fcntl(master_fd, fcntl.F_SETFL, fl | os.O_NONBLOCK)

                proc = await asyncio.create_subprocess_exec(
                    *cmd,
                    stdin=slave_fd,
                    stdout=slave_fd,
                    stderr=slave_fd,
                    close_fds=True
                )
                os.close(slave_fd)

                loop = asyncio.get_running_loop()
                buffer = ""

                while True:
                    try:
                        data = await loop.run_in_executor(None, os.read, master_fd, 4096)
                        if not data:
                            break
                        text = data.decode('utf-8', errors='replace')
                        buffer += text

                        for p_name, rule_re, p_resp in PROMPT_RULES:
                            if rule_re.search(text):
                                try:
                                    os.write(master_fd, p_resp.encode('utf-8'))
                                    self.log_system(f"Auto-responded to prompt ({p_name})")
                                except OSError:
                                    pass
                                break

                        speed_match = SPEED_ETA_REGEX.search(buffer)
                        pct_match = PCT_REGEX.search(buffer)
                        if speed_match:
                            self.update_telemetry(f"Running {task.script_name}", f"{speed_match.group(1)} (ETA {speed_match.group(2)})")
                        elif pct_match:
                            self.update_telemetry(f"Running {task.script_name} ({pct_match.group(0)})")

                        while "\r" in buffer or "\n" in buffer:
                            r_idx = buffer.find("\r")
                            n_idx = buffer.find("\n")
                            if r_idx != -1 and (n_idx == -1 or r_idx < n_idx):
                                line, buffer = buffer[:r_idx], buffer[r_idx+1:]
                            else:
                                line, buffer = buffer[:n_idx], buffer[n_idx+1:]

                            stripped = ANSI_STRIP_REGEX.sub('', line).strip()
                            if not stripped:
                                continue
                            if PROGRESS_BAR_REGEX.search(line) and len(line) < 80 and not ("Error" in line or "ERR" in line):
                                continue

                            self.log_task(line + "\n")

                    except (OSError, BlockingIOError):
                        if proc.returncode is not None:
                            break
                        await asyncio.sleep(0.05)

                rc = await proc.wait()
                if buffer:
                    stripped = ANSI_STRIP_REGEX.sub('', buffer).strip()
                    if stripped and not PROGRESS_BAR_REGEX.search(buffer):
                        self.log_task(stripped + "\n")

                if rc == 0:
                    await self.task_success(task)
                else:
                    if task.ignore_fail:
                        self.log_system(f"Task exited with status {rc} but ignore_fail is active. Proceeding.")
                        await self.task_success(task)
                    else:
                        await self.task_failure(task, f"Process exited with status code {rc}")
            except Exception as e:
                await self.task_failure(task, str(e))
            finally:
                try: os.close(master_fd)
                except OSError: pass

    async def task_success(self, task: OrchestratorTask):
        self.update_task_status(self.current_idx, TaskStatus.COMPLETED)
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

    def task_skipped(self, task: OrchestratorTask):
        self.update_task_status(self.current_idx, TaskStatus.SKIPPED)
        self.log_system(f"Skipped task: {task.script_name}")
        self.progress_bar.advance(1)
        self.current_idx += 1
        self.run_worker(self.run_execution_loop())

    async def task_failure(self, task: OrchestratorTask, reason: str):
        self.update_task_status(self.current_idx, TaskStatus.FAILED)
        self.log_task(f"\n\033[1;31m>>> EXECUTION FAILED: {reason}\033[0m")
        if self.stop_on_fail:
            self.log_system("stop-on-fail active. Terminating installer phase.")
            await asyncio.sleep(1.5)
            self.exit(1)
        else:
            res = await self.push_screen_wait(FailureModalScreen(task.script_name, reason))
            if res == "retry":
                self.run_worker(self.run_execution_loop())
            elif res == "skip":
                self.task_skipped(task)
            else:
                self.exit(1)

    def action_quit_app(self):
        self.exit(1)

    def action_toggle_manual(self):
        self.manual = not self.manual
        mode = "ENABLED" if self.manual else "DISABLED"
        self.log_system(f"Manual step confirmation mode {mode}")

    def action_reset_state(self):
        if self.state_file.exists():
            try:
                self.state_file.unlink()
                self.completed_keys.clear()
                self.log_system("Phase completion state reset.")
            except Exception as e:
                self.log_system(f"Failed to reset state: {e}")

# ==============================================================================
# MAIN ENTRYPOINT
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
    
    if args.profile:
        custom_path = Path(args.profile)
        if custom_path.exists() and custom_path.is_file():
            try:
                selected_profile = load_profile(custom_path)
            except Exception as e:
                sys.stderr.write(f"Error loading custom profile from {custom_path}: {e}\n")
                sys.exit(1)
        else:
            for p in profiles:
                if p.name == args.profile or (p.filepath and p.filepath.stem == args.profile):
                    selected_profile = p
                    break
            if not selected_profile:
                sys.stderr.write(f"Error: Installer profile '{args.profile}' not found.\n")
                sys.exit(1)
    else:
        for p in profiles:
            if p.filepath and p.filepath.name.startswith("001_") and p.filepath.name.endswith(".toml"):
                selected_profile = p
                break
        if not selected_profile and profiles:
            selected_profile = profiles[0]

    if not selected_profile:
        sys.stderr.write(f"Error: No valid installer profile found in '{PROFILES_DIR}'. Installation aborted.\n")
        sys.exit(1)

    profile_name = selected_profile.name
    raw_sequence = selected_profile.phase1_tasks if phase1 else selected_profile.phase2_tasks
    
    tasks: List[OrchestratorTask] = []
    for i, t in enumerate(raw_sequence):
        resolved_path = SCRIPT_DIR / t.script_name
        if not resolved_path.exists():
            resolved_path = None
            
        interpreter = t.interpreter
        is_interactive = t.interactive
        if resolved_path:
            interpreter, file_interactive = resolve_interpreter(resolved_path)
            if file_interactive:
                is_interactive = True
            
        state_key = hashlib.md5(f"{i}:{t.script_name}:{'-'.join(t.args)}".encode()).hexdigest()

        tasks.append(OrchestratorTask(
            script_name=t.script_name,
            args=t.args,
            ignore_fail=t.ignore_fail,
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
