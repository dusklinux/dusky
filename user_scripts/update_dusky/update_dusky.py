#!/usr/bin/env python3
# ==============================================================================
#  DUSKY UPDATER (v9.4.1) — BLEEDING EDGE ARCH / PYTHON 3.14 TUI
# ==============================================================================
import asyncio
import json
import shutil
import subprocess
import sys
import importlib.util
import importlib
import site
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Literal, Optional

# --- Enforcement: Bleeding-Edge Python 3.14+ ---
if sys.version_info < (3, 14):
    sys.stdout.write("\033[1;31m[FATAL]\033[0m Dusky requires Python 3.14+ bleeding-edge architecture.\n")
    sys.exit(1)

# ==============================================================================
#  PRE-FLIGHT BOOTSTRAP & DEPENDENCY RESOLUTION
# ==============================================================================
def verify_sudo() -> bool:
    sys.stdout.write("\033[1;36m[DUSKY PRE-FLIGHT]\033[0m Securing administrative kernel privileges...\n")
    try:
        subprocess.run(['sudo', '-v'], check=True)
        return True
    except subprocess.CalledProcessError:
        sys.stdout.write("\033[1;31m[FATAL]\033[0m Sudo authentication rejected. Aborting.\n")
        return False

def bootstrap_dependencies() -> bool:
    missing = [pkg for mod, pkg in [("textual", "python-textual"), ("rich", "python-rich")] if importlib.util.find_spec(mod) is None]
    if missing:
        sys.stdout.write(f"\033[1;33m[DUSKY BOOTSTRAP]\033[0m Resolving dependencies: {', '.join(missing)}\n")
        if not verify_sudo(): sys.exit(1)
        try:
            subprocess.run(['sudo', 'pacman', '-S', '--noconfirm'] + missing, check=True)
            # Reconstruct module paths forcefully to accommodate identical runtime continuity
            importlib.invalidate_caches()
            importlib.reload(site)
        except subprocess.CalledProcessError:
            sys.stdout.write("\033[1;31m[FATAL]\033[0m Dependency resolution failed.\n")
            sys.exit(1)
        return True
    return False

SUDO_ALREADY_ACQUIRED = bootstrap_dependencies()

try:
    from rich.text import Text
    from rich.markup import escape
    from rich.syntax import Syntax
    from textual.app import App, ComposeResult
    from textual.containers import Horizontal, Vertical
    from textual.widgets import RichLog, Static, ProgressBar, ListView, ListItem, Label, ContentSwitcher
    from textual.reactive import reactive
except ImportError:
    sys.stdout.write("\033[1;31m[FATAL]\033[0m UI library import failed post-resolution. Ensure Arch mirrors are synced.\n")
    sys.exit(1)

# ==============================================================================
#  THEME COMPILER (MATUGEN JSON)
# ==============================================================================
def compile_theme() -> dict[str, str]:
    theme: dict[str, str] = {
        "bg": "#1a110e", "fg": "#f1dfd9", "accent": "#ffb59b",
        "error": "#ffb4ab", "warning": "#e7bdaf", "success": "#d5c68e", "muted": "#53433e"
    }
    theme_path = Path.home() / ".config/matugen/generated/dusky_tui.json"
    if theme_path.is_file():
        try:
            data = json.loads(theme_path.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                theme.update({str(k): str(v) for k, v in data.items()})
        except (json.JSONDecodeError, OSError):
            pass
    return theme

THEME = compile_theme()

# --- UTILITY: RESILIENT RGB TRANSLATION ---
def get_rgb_color(hex_str: str, default: tuple[int, int, int] = (255, 181, 155)) -> tuple[int, int, int]:
    try:
        clean_hex = hex_str.lstrip('#')
        if len(clean_hex) >= 6:
            return int(clean_hex[0:2], 16), int(clean_hex[2:4], 16), int(clean_hex[4:6], 16)
        elif len(clean_hex) == 3:
            return int(clean_hex[0]*2, 16), int(clean_hex[1]*2, 16), int(clean_hex[2]*2, 16)
    except (ValueError, IndexError, Exception):
        pass
    return default

# --- ULTRA-MODERN MINIMAL CSS ARCHITECTURE ---
# Eradicated fallacious ':active' pseudo-class invocation to strictly conform with Textual lexer axioms.
DUSKY_CSS = f"""
Screen {{ background: {THEME['bg']}; color: {THEME['fg']}; }}
#sidebar {{
    width: 35%; 
    border-right: solid {THEME['muted']}4d; 
    background: {THEME['bg']};
    height: 100%;
    scrollbar-size-vertical: 1;
}}
#log_container {{ 
    width: 65%; padding: 0; 
    background: {THEME['bg']}; 
    height: 100%;
}}
ContentSwitcher {{ height: 1fr; width: 100%; }}
RichLog {{
    height: 1fr; background: transparent; color: {THEME['fg']};
    border: none; padding: 1 2;
    scrollbar-size-vertical: 1;
}}
ScrollBar {{
    background: transparent;
}}
ScrollBar > .scrollbar--track {{
    background: transparent;
}}
ScrollBar > .scrollbar--bar {{
    color: {THEME['accent']}66;
}}
ScrollBar > .scrollbar--bar:hover {{
    color: {THEME['accent']}cc;
}}
ListView {{ background: transparent; overflow-x: hidden; height: 100%; scrollbar-size-vertical: 1; }}
ListItem {{ 
    padding: 0 1; 
    border-left: tall transparent;
    background: transparent;
}}
ListItem:focus {{ 
    background: {THEME['accent']}1a; 
    border-left: tall {THEME['accent']};
}}
.header-panel {{
    dock: top; height: 1; 
    background: {THEME['bg']}; 
    color: {THEME['accent']};
    content-align: center middle; 
    text-style: bold;
    border-bottom: solid {THEME['muted']}4d;
}}
ProgressBar {{ dock: bottom; margin: 0; height: 1; }}
ProgressBar > .progress--bar {{ color: {THEME['accent']}; }}
ProgressBar > .progress--remaining {{ background: {THEME['muted']}33; }}
"""

# ==============================================================================
#  MANIFEST & PATH CONSTANTS
# ==============================================================================
WORK_TREE = Path.home()
GIT_DIR = WORK_TREE / "dusky"
BACKUP_BASE_DIR = WORK_TREE / "Documents" / "dusky_backups"

REPO_URL = "https://github.com/veltraced/dusker"
BRANCH = "main"

# Topologically decoupled search heuristics replacing static path dictionaries.
SCRIPT_SEARCH_DIRS = [
    "user_scripts/arch_setup_scripts/scripts",
    "user_scripts/arch_setup_scripts",
    "user_scripts/rofi",
    "user_scripts/theme_matugen",
    "user_scripts/theme_matugen/config",
    "user_scripts/theme_matugen/firefox",
    "user_scripts/btrfs_snapshots",
    "user_scripts/tts_stt/dusky_kokoro",
    "user_scripts/tts_stt/dusky_parakeet",
    "user_scripts/networking",
    "user_scripts/misc_extra",
    "user_scripts/misc_extra/delete_in_3_weeks",
    "user_scripts/update_dusky/update_checker",
    "user_scripts/update_dusky",
    "user_scripts/dusky_system/reload_cc",
    "user_scripts/services",
    "user_scripts/waybar"
]

UPDATE_SEQUENCE = [
    "U | backup_hyprlang_files.sh",
    "U | dusky_commands_before.sh",
    "U | 005_hypr_custom_config_setup.sh",
    "U | 005_hypr_custom_config_setup.sh --trackpad --autostart --force",
    "U | 010_package_removal.sh --auto",
    "S | interactive | pacman_packages.sh",
    "U | interactive | paru_packages.sh",
    "U | rofi_wallpaper_selctor.sh --cache-only --progress",
    "U | 015_set_thunar_terminal_kitty.sh",
    "U | 020_desktop_apps_username_setter.sh --quiet",
    "U | 025_configure_keyboard.sh",
    "S | 051_pacman_hooks.sh --auto",
    "U | 130_copy_service_files.sh --default",
    "U | 131_dbus_copy_service_files.sh",
    "U | 170_waypaper_config_reset.sh",
    "U | 235_file_manager_switch.sh --apply-state",
    "U | 236_browser_switcher.sh --apply-state",
    "U | 237_text_editer_switcher.sh --apply-state",
    "U | 238_terminal_switcher.sh --apply-state",
    "U | 402_gecko_engine_colors_extention.sh",
    "U | 434_wayclick_soundpacks_download.sh --auto",
    "U | 455_hyprctl_reload.sh",
    "S | 473_add_user_to_group.sh --auto",
    "S | 485_sudoers_nopassword.sh",
    "U | copy_service_files.sh --default",
    "U | update_checker.sh --num",
    "U | cc_restart.sh --quiet",
    "S | dusky_service_manager.sh",
    "U | ignore-fail | interactive | dusky_matugen_config_tui.sh --smart",
    "U | ignore-fail | hypr_anim.sh --current",
    "U | ignore-fail | theme_ctl.sh refresh",
    "U | ignore-fail | update_counter.sh",
    "U | dusky_commands_after.sh",
    "U | interactive | system_update.sh --pacman",
    "U | interactive | reboot_post_lua_update.sh"
]

def resolve_script_path(script_name: str) -> Optional[Path]:
    """
    Executes a heuristic directory traversal to dynamically locate scripts.
    CRITICAL: This must only be invoked POST-Git synchronization to ensure
    newly pulled topological additions are correctly identified.
    """
    if "/" in script_name:
        p = Path(script_name)
        absolute_target = p if p.is_absolute() else WORK_TREE / p
        return absolute_target if absolute_target.is_file() else None
        
    for directory in SCRIPT_SEARCH_DIRS:
        candidate = WORK_TREE / directory / script_name
        if candidate.is_file():
            return candidate
            
    return None

# ==============================================================================
#  STRUCTURAL PATTERN MATCHING & PARSING
# ==============================================================================
@dataclass
class DuskyTask:
    name: str
    mode: Literal['U', 'S', 'GIT']
    ignore_fail: bool
    interactive: bool
    args: list[str]
    status: Literal['pending', 'running', 'success', 'failed', 'skipped'] = 'pending'

def parse_manifest(sequence: list[str]) -> list[DuskyTask]:
    tasks = [
        DuskyTask("Git Bare Repo Validation", 'GIT', False, False, []),
        DuskyTask("Fetch Upstream & Diff", 'GIT', False, False, []),
        DuskyTask("Forensic Collision Backup", 'GIT', False, False, []),
        DuskyTask("Atomic Snapshot (CoW)", 'GIT', False, False, []),
        DuskyTask("Apply Bare Updates (Reset)", 'GIT', False, False, [])
    ]

    interactive_heuristics = {'reboot_post_lua_update.sh', 'dusky_matugen_config_tui.sh', 'dusky_firefox_tui.sh'}

    for entry in sequence:
        entry = entry.strip()
        if not entry or entry.startswith('#'): continue
        
        parts = [p.strip() for p in entry.split('|')]
        
        match parts:
            case [mode, *middle_flags, cmd_part]:
                flags = {f.lower() for block in middle_flags for f in block.split()}
                cmd_tokens = cmd_part.split()
            case _:
                continue
                
        if not cmd_tokens: continue
        
        script_name, *args = cmd_tokens
        
        ignore_fail = bool(flags.intersection({"ignore", "ignore-fail", "true"}))
        interactive = bool(flags.intersection({"interactive", "tui", "prompt"}))
        
        if not interactive and any(script_name.startswith(s) for s in interactive_heuristics):
            interactive = True

        tasks.append(DuskyTask(
            name=script_name, mode=mode, # type: ignore
            ignore_fail=ignore_fail, interactive=interactive,
            args=args
        ))
    return tasks

# ==============================================================================
#  GIT ASYNCHRONOUS ENGINE
# ==============================================================================
class GitEngine:
    def __init__(self, app: App):
        self.app = app
        self.log = app.log_main # type: ignore
        self.git_cmd_base = ['git', f'--git-dir={GIT_DIR}', f'--work-tree={WORK_TREE}']
        BACKUP_BASE_DIR.mkdir(parents=True, exist_ok=True)

    async def _run(self, *args: str, check: bool = True, task_idx: int = -1) -> tuple[int, str, str]:
        cmd = self.git_cmd_base + list(args)
        proc = await asyncio.create_subprocess_exec(
            *cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
        )
        stdout, stderr = await proc.communicate()
        out, err = stdout.decode('utf-8', errors='replace').strip(), stderr.decode('utf-8', errors='replace').strip()
        
        if task_idx != -1 and err:
            self.app.log_task(escape(err), task_idx) # type: ignore

        if proc.returncode != 0 and check:
            msg = f"[bold {THEME['error']}]Git Architecture Error ({proc.returncode}):[/] {escape(err)}"
            self.log(msg)
            if task_idx != -1: self.app.log_task(msg, task_idx) # type: ignore
            raise subprocess.CalledProcessError(proc.returncode, cmd, output=out, stderr=err)
        return proc.returncode, out, err

    async def _clear_git_locks(self, task_idx: int):
        """Clears stale Git lock files that precipitate catastrophic 'Could not reset index' failures."""
        for lock in ['index.lock', 'config.lock', 'HEAD.lock', 'ORIG_HEAD.lock', 'FETCH_HEAD.lock']:
            lock_path = GIT_DIR / lock
            if lock_path.exists():
                try:
                    lock_path.unlink()
                    self.app.log_task(f"[dim]Cleared stale Git lock: {lock}[/dim]", task_idx) # type: ignore
                except Exception as e:
                    self.app.log_task(f"[bold {THEME['warning']}]Failed to clear {lock}: {e}[/]", task_idx) # type: ignore

    def _collect_dir_collision_roots(self, root_rel: str, tracked_exact: set, tracked_descendants: set, out_set: set):
        stack = [root_rel]
        while stack:
            rel = stack.pop()
            abs_path = WORK_TREE / rel
            if not (abs_path.exists() or abs_path.is_symlink()): continue
            
            if abs_path.is_symlink() or not abs_path.is_dir():
                if rel not in tracked_exact:
                    out_set.add(rel)
                continue
                
            if rel in tracked_exact:
                out_set.add(rel)
                continue
                
            try:
                children = [p.name for p in abs_path.iterdir()]
            except OSError:
                children = []

            if rel in tracked_descendants:
                if not children:
                    out_set.add(rel)
                else:
                    for child in children:
                        stack.append(f"{rel}/{child}")
            else:
                out_set.add(rel)

    async def _backup_worktree_collisions(self, ref: str, honor_tracked: bool, task_idx: int) -> bool:
        _, ls_tree, _ = await self._run('ls-tree', '-r', '-z', '--name-only', ref, task_idx=-1)
        incoming = [f for f in ls_tree.split('\0') if f]

        tracked_exact = set()
        tracked_descendants = set()

        if honor_tracked:
            _, ls_files, _ = await self._run('ls-files', '-z', task_idx=-1)
            for f in ls_files.split('\0'):
                if not f: continue
                tracked_exact.add(f)
                p = Path(f)
                while str(p.parent) != '.':
                    p = p.parent
                    tracked_descendants.add(str(p))

        collision_candidates = set()

        for target_path in incoming:
            abs_path = WORK_TREE / target_path
            
            if abs_path.exists() or abs_path.is_symlink():
                if abs_path.is_dir() and not abs_path.is_symlink():
                    if honor_tracked and target_path in tracked_descendants:
                        self._collect_dir_collision_roots(target_path, tracked_exact, tracked_descendants, collision_candidates)
                    else:
                        collision_candidates.add(target_path)
                elif not honor_tracked or target_path not in tracked_exact:
                    collision_candidates.add(target_path)
                    
            ancestor = ""
            remaining = target_path
            while '/' in remaining:
                part, remaining = remaining.split('/', 1)
                ancestor = f"{ancestor}/{part}" if ancestor else part
                abs_ancestor = WORK_TREE / ancestor
                
                if abs_ancestor.exists() or abs_ancestor.is_symlink():
                    if abs_ancestor.is_symlink() or not abs_ancestor.is_dir():
                        if not honor_tracked or ancestor not in tracked_exact:
                            collision_candidates.add(ancestor)
                        break

        collision_roots = set()
        for coll in collision_candidates:
            skip = False
            p = Path(coll)
            while str(p.parent) != '.':
                p = p.parent
                if str(p) in collision_candidates:
                    skip = True
                    break
            if not skip:
                collision_roots.add(coll)

        if not collision_roots:
            self.app.log_task(f"[bold {THEME['success']}]No structural filesystem conflicts detected.[/]", task_idx) # type: ignore
            return True

        backup_dir = BACKUP_BASE_DIR / f"untracked_collisions_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
        backup_dir.mkdir(parents=True, exist_ok=True)

        for coll in collision_roots:
            src = WORK_TREE / coll
            if not (src.exists() or src.is_symlink()): continue
            
            dest = backup_dir / coll
            dest.parent.mkdir(parents=True, exist_ok=True)
            try:
                shutil.move(str(src), str(dest))
                self.app.log_task(f"[dim]Secured structural conflict: {escape(coll)}[/dim]", task_idx) # type: ignore
            except Exception as e:
                self.app.log_task(f"[bold {THEME['warning']}]Failed to backup collision {escape(coll)}: {escape(str(e))}[/]", task_idx) # type: ignore

        msg = f"[bold {THEME['warning']}]Secured {len(collision_roots)} structural tree collisions.[/]"
        self.log(msg)
        self.app.log_task(msg, task_idx) # type: ignore
        return True

    async def execute_phase(self) -> bool:
        try:
            # ---------------------------------------------------------
            # Task 0: Bare Repo Validation & Lock Clearance
            # ---------------------------------------------------------
            idx = 0
            self.app.update_task_state(idx, "running") # type: ignore
            self.app.log_task(f"[bold {THEME['accent']}]>>> PROCESS INITIATED:[/] Bare Repository Validation\n", idx) # type: ignore
            
            await self._clear_git_locks(idx)

            if not GIT_DIR.exists():
                msg = f"[bold {THEME['warning']}]Bare repository missing. Initiating bleeding-edge clone...[/]"
                self.log(msg); self.app.log_task(msg, idx) # type: ignore
                
                proc = await asyncio.create_subprocess_exec(
                    'git', 'clone', '--bare', '--branch', BRANCH, REPO_URL, str(GIT_DIR),
                    stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT
                )
                if proc.stdout:
                    async for line in proc.stdout: 
                        decoded = line.decode('utf-8', errors='replace').strip()
                        self.log(f"[dim]{escape(decoded)}[/dim]")
                        self.app.log_task(f"[dim]{escape(decoded)}[/dim]", idx) # type: ignore
                await proc.wait()
                
                if proc.returncode != 0: raise RuntimeError("Clone sequence failed.")
                await self._run('config', 'status.showUntrackedFiles', 'no', task_idx=idx)
            else:
                self.app.log_task(f"[bold {THEME['success']}]Bare repository integrity verified.[/]", idx) # type: ignore
            
            self.app.update_task_state(idx, "success") # type: ignore

            # ---------------------------------------------------------
            # Task 1: Fetch Upstream & Diff
            # ---------------------------------------------------------
            idx = 1
            self.app.update_task_state(idx, "running") # type: ignore
            self.app.log_task(f"[bold {THEME['accent']}]>>> PROCESS INITIATED:[/] Fetch Upstream & Diff\n", idx) # type: ignore
            
            msg = "[dim]Synchronizing upstream references...[/dim]"
            self.log(msg); self.app.log_task(msg, idx) # type: ignore
            
            await self._run('fetch', 'origin', f'+refs/heads/{BRANCH}:refs/remotes/origin/{BRANCH}', task_idx=idx)
            
            rc, local_head, _ = await self._run('rev-parse', '--verify', '-q', 'HEAD', check=False, task_idx=-1)
            local_head = local_head.strip() if rc == 0 else ""
            
            rc, remote_head, _ = await self._run('rev-parse', '--verify', '-q', f'origin/{BRANCH}', check=False, task_idx=-1)
            remote_head = remote_head.strip() if rc == 0 else ""
            
            diff_out = ""
            if local_head and remote_head:
                _, diff_out, _ = await self._run('diff', f'{local_head}..{remote_head}', check=False, task_idx=-1)
                self.app.git_diff_text = diff_out # type: ignore
                
                if diff_out.strip():
                    self.app.log_task(f"\n[bold {THEME['warning']}]Differential Divergence Detected:[/]\n", idx) # type: ignore
                    self.app.log_task(Syntax(diff_out, "diff", theme="monokai", background_color="default", word_wrap=True), idx) # type: ignore
                else:
                    self.app.log_task(f"[bold {THEME['success']}]State matched. Zero differential divergence found.[/]", idx) # type: ignore
            
            if local_head and local_head == remote_head:
                msg = f"\n[bold {THEME['success']}]Repository synchronization perfect. Origin matched.[/]"
                self.log(msg); self.app.log_task(msg, idx) # type: ignore
                self.app.update_task_state(idx, "success") # type: ignore
                for i in range(2, 5): self.app.update_task_state(i, "skipped") # type: ignore
                return True
            
            if not local_head:
                behind = "all (bootstrap)"
            else:
                _, behind, _ = await self._run('rev-list', '--count', f'{local_head}..{remote_head}', task_idx=-1)
                
            msg = f"\n[bold {THEME['accent']}]Local branch behind by {behind} commits.[/]"
            self.log(msg); self.app.log_task(msg, idx) # type: ignore
            self.app.update_task_state(idx, "success") # type: ignore

            # ---------------------------------------------------------
            # Task 2: Forensic Collision Backup
            # ---------------------------------------------------------
            idx = 2
            self.app.update_task_state(idx, "running") # type: ignore
            self.app.log_task(f"[bold {THEME['accent']}]>>> PROCESS INITIATED:[/] Forensic Collision Backup\n", idx) # type: ignore
            
            await self._backup_worktree_collisions(f'origin/{BRANCH}', honor_tracked=True, task_idx=idx)
            self.app.update_task_state(idx, "success") # type: ignore

            # ---------------------------------------------------------
            # Task 3: BTRFS CoW Snapshot (Safe Skip on Initial Setup)
            # ---------------------------------------------------------
            idx = 3
            self.app.update_task_state(idx, "running") # type: ignore
            self.app.log_task(f"[bold {THEME['accent']}]>>> PROCESS INITIATED:[/] Atomic Snapshot (CoW)\n", idx) # type: ignore
            
            if local_head:
                _, diff, _ = await self._run('diff-index', '--name-only', 'HEAD', check=False, task_idx=-1)
                modified = [f for f in diff.split('\n') if f.strip()]
                if modified:
                    snap_dir = BACKUP_BASE_DIR / f"cow_snapshot_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
                    snap_dir.mkdir(parents=True)
                    copied = 0
                    for file in modified:
                        src = WORK_TREE / file
                        if src.exists() or src.is_symlink():
                            dest = snap_dir / file
                            dest.parent.mkdir(parents=True, exist_ok=True)
                            
                            # CRITICAL FIX: Explicit process await prevents catastrophic race condition
                            # against the 'git reset --hard' mechanism that executes next.
                            proc = await asyncio.create_subprocess_exec('cp', '-a', '--reflink=auto', str(src), str(dest))
                            await proc.wait()
                            
                            self.app.log_task(f"[dim]CoW Snapshot: {escape(file)}[/dim]", idx) # type: ignore
                            copied += 1
                    msg = f"[bold {THEME['success']}]Atomic BTRFS CoW Snapshot secured ({copied} items).[/]"
                    self.log(msg); self.app.log_task(msg, idx) # type: ignore
                else:
                    self.app.log_task(f"[bold {THEME['success']}]No local tracked modifications found. Snapshot skipped.[/]", idx) # type: ignore
            else:
                self.app.log_task(f"[bold {THEME['success']}]Initial bootstrap phase. Tracked references empty. Snapshot skipped.[/]", idx) # type: ignore
                
            self.app.update_task_state(idx, "success") # type: ignore

            # ---------------------------------------------------------
            # Task 4: Apply Reset
            # ---------------------------------------------------------
            idx = 4
            self.app.update_task_state(idx, "running") # type: ignore
            self.app.log_task(f"[bold {THEME['accent']}]>>> PROCESS INITIATED:[/] Apply Bare Updates (Reset)\n", idx) # type: ignore
            
            await self._run('reset', '--hard', f'origin/{BRANCH}', task_idx=idx)
            
            msg = f"[bold {THEME['success']}]Bare Repository reset applied and synchronized.[/]"
            self.log(msg); self.app.log_task(msg, idx) # type: ignore
            self.app.update_task_state(idx, "success") # type: ignore
            
            return True

        except Exception as e:
            self.log(f"[bold {THEME['error']}][FATAL][/] Architecture Reconciliation Failure: {escape(str(e))}")
            for i in range(5):
                if self.app.tasks[i].status == "running": # type: ignore
                    self.app.update_task_state(i, "failed") # type: ignore
                elif self.app.tasks[i].status == "pending": # type: ignore
                    self.app.update_task_state(i, "skipped") # type: ignore
            return False

# ==============================================================================
#  TEXTUAL UI COMPONENTS
# ==============================================================================
class MainLogItem(ListItem):
    def compose(self) -> ComposeResult:
        yield Label(f" [bold {THEME['accent']}]CORE[/] Dusky Execution Engine", classes="list-item-label")


class TaskItem(ListItem):
    status = reactive("pending")

    def __init__(self, task: DuskyTask, index: int):
        super().__init__()
        self.dusky_task = task
        self.task_index = index

    def compose(self) -> ComposeResult:
        yield Label(id=f"lbl-{self.task_index}")

    def on_mount(self) -> None:
        self._update_label()

    def watch_status(self, old_status: str, new_status: str) -> None:
        self._update_label()

    def _update_label(self) -> None:
        if not self.is_mounted:
            return

        if self.dusky_task.mode == 'GIT':
            badge = f"[bold {THEME['accent']}]GIT[/]"
        elif self.dusky_task.mode == 'S':
            badge = f"[bold {THEME['error']}]SUDO[/]"
        else:
            badge = f"[bold {THEME['success']}]USER[/]"
        
        cmd_str = f"{self.dusky_task.name} {' '.join(self.dusky_task.args)}".strip()
        if len(cmd_str) > 31: cmd_str = cmd_str[:28] + "..."
        cmd_str = escape(cmd_str)

        suffix = ""
        if self.dusky_task.name == "Fetch Upstream & Diff" and getattr(self.app, 'git_diff_text', None) and self.status in ("success", "skipped"):
            suffix = f" [dim {THEME['success']}](Diff recorded)[/]"

        icons = {
            'pending': f"[dim {THEME['muted']}]○[/]", 
            'running': f"[bold {THEME['accent']} blink]◉[/]", 
            'success': f"[bold {THEME['success']}]✓[/]", 
            'failed':  f"[bold {THEME['error']}]✗[/]", 
            'skipped': f"[dim {THEME['warning']}]-[/]"
        }
        icon = icons.get(self.status, "❓")

        color_map = {
            'running': f"bold {THEME['fg']}", 'pending': f"dim {THEME['muted']}",
            'success': f"bold {THEME['success']}", 'failed': f"bold {THEME['error']}",
            'skipped': f"dim {THEME['warning']}"
        }
        color = color_map.get(self.status, "white")
        
        try:
            self.query_one(Label).update(f" {icon}  {badge}  [{color}]{cmd_str}[/]{suffix}")
        except Exception:
            pass

# ==============================================================================
#  MAIN APPLICATION ENGINE
# ==============================================================================
class DuskyApp(App):
    CSS = DUSKY_CSS

    def __init__(self, tasks: list[DuskyTask], has_sudo: bool):
        super().__init__()
        self.tasks = tasks
        self.has_sudo = has_sudo
        self.abort_flag = False
        self.git_diff_text = ""

    def compose(self) -> ComposeResult:
        yield Static(" 🦅 DUSKY PIPELINE ENGINE (v9.4.1 — Elegance Edition)", classes="header-panel")
        
        with Horizontal():
            with Vertical(id="sidebar"):
                yield ListView(id="task_list")
            
            with Vertical(id="log_container"):
                with ContentSwitcher(initial="log-main", id="log_switcher"):
                    yield RichLog(id="log-main", markup=True, wrap=True, auto_scroll=True)
                    for i in range(len(self.tasks)):
                        yield RichLog(id=f"log-task-{i}", markup=True, wrap=True, auto_scroll=True)
                
        yield ProgressBar(total=len(self.tasks), id="main_progress", show_eta=False)

    async def on_mount(self) -> None:
        self.progress = self.query_one("#main_progress", ProgressBar)
        
        list_view = self.query_one("#task_list", ListView)
        list_view.append(MainLogItem())
        for i, task in enumerate(self.tasks):
            list_view.append(TaskItem(task, i))

        self.log_main(f"[bold {THEME['accent']}]======================================================[/]")
        self.log_main(f"[bold {THEME['fg']}] ARCHITECTURE INITIALIZATION — {datetime.now().strftime('%H:%M:%S')}[/]")
        self.log_main(f"[bold {THEME['accent']}]======================================================[/]")
        
        # RELIABILITY FIX: Native Textual Timer binds sudo keepalive strictly to App lifecycle
        # preventing resource leak out-of-bounds.
        if self.has_sudo:
            self.set_interval(60.0, self.ping_sudo)
        
        self.run_worker(self.execute_pipeline(), exclusive=True, thread=False)

    def log_main(self, message: str) -> None:
        self.query_one("#log-main", RichLog).write(message)

    def log_task(self, message: any, index: int) -> None:
        try:
            self.query_one(f"#log-task-{index}", RichLog).write(message)
        except Exception:
            pass

    def update_task_state(self, index: int, new_status: str) -> None:
        self.tasks[index].status = new_status # type: ignore
        list_view = self.query_one("#task_list", ListView)
        
        try:
            task_nodes = list_view.query(TaskItem).nodes
            if index < len(task_nodes):
                task_nodes[index].status = new_status
        except Exception:
            pass
            
        if new_status == "running" and list_view.index in [None, 0, index]:
            list_view.index = index + 1
            
        if new_status in ("success", "failed", "skipped"):
            self.progress.advance(1)

    def on_list_view_highlighted(self, event: ListView.Highlighted) -> None:
        item = event.item
        if item is None:
            return
        switcher = self.query_one("#log_switcher", ContentSwitcher)
        if isinstance(item, MainLogItem):
            switcher.current = "log-main"
        elif isinstance(item, TaskItem):
            switcher.current = f"log-task-{item.task_index}"

    async def ping_sudo(self) -> None:
        """Lightweight background keep-alive tied inherently to the app runtime lifecycle."""
        try:
            proc = await asyncio.create_subprocess_exec(
                'sudo', '-n', '-v', stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL
            )
            await proc.wait()
        except Exception:
            pass

    async def execute_pipeline(self) -> None:
        self.log_main(f"\n[bold {THEME['accent']}]═══ Phase 1: Git Architecture Reconciliation ═══[/]\n")
        
        git_engine = GitEngine(self)
        if not await git_engine.execute_phase():
            self.abort_flag = True
            self.log_main(f"\n[bold {THEME['error']} blink]SYSTEM HALTED. GIT INTEGRITY VIOLATION.[/]")
            
            # Synchronize pipeline list state to match visual halt
            for index in range(5, len(self.tasks)):
                self.update_task_state(index, "skipped")
            return

        self.log_main(f"\n[bold {THEME['accent']}]═══ Phase 2: Configuration Pipeline Execution ═══[/]\n")

        success_count, fail_count = 0, 0

        for index in range(5, len(self.tasks)):
            if self.abort_flag: 
                self.update_task_state(index, "skipped")
                continue

            task = self.tasks[index]
            self.update_task_state(index, "running")
            
            cmd_str = f"{task.name} {' '.join(task.args)}".strip()
            self.log_main(f"\n[bold {THEME['warning']}]>[/] Executing Process: [bold {THEME['fg']}]{escape(cmd_str)}[/]")
            self.log_task(f"[bold {THEME['accent']}]>>> PROCESS INITIATED:[/] {escape(cmd_str)}\n", index)

            # Heuristic Path Resolution executes here to accommodate temporally novel scripts introduced via upstream pull.
            resolved_path = resolve_script_path(task.name)

            if not resolved_path:
                err = f"[bold {THEME['error']}][ERROR][/] Architecture File Missing: {escape(task.name)}"
                self.log_main(err); self.log_task(err, index)
                self.update_task_state(index, "failed")
                fail_count += 1
                if not task.ignore_fail: self.abort_flag = True
                continue

            exec_cmd = [str(resolved_path)] + task.args
            if task.mode == 'S': exec_cmd = ['sudo', '-n'] + exec_cmd
            
            try:
                if task.interactive:
                    self.log_main(f"[dim]Suspending UI abstraction... Passing raw PTY control...[/]")
                    self.log_task(f"[dim]Interactive flag detected. Console control delegated to user.[/]", index)
                    
                    with self.suspend():
                        r, g, b = get_rgb_color(THEME['accent'])
                        sys.stdout.write(f"\n\033[1;38;2;{r};{g};{b}m=== DUSKY INTERACTIVE ABSTRACTION: {task.name} ===\033[0m\n\n")
                        sys.stdout.flush()
                        
                        proc = await asyncio.create_subprocess_exec(*exec_cmd, cwd=str(WORK_TREE))
                        await proc.wait()
                        rc = proc.returncode
                        
                        sys.stdout.write(f"\n\033[1;38;2;{r};{g};{b}m=== ABSTRACTION TERMINATED (Code: {rc}) ===\033[0m\n")
                        sys.stdout.flush()
                    
                    self.log_task(f"\n[bold {THEME['success']}]PTY control returned. Exit Code: {rc}[/]", index)
                
                else:
                    proc = await asyncio.create_subprocess_exec(
                        *exec_cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT, cwd=str(WORK_TREE)
                    )
                    if proc.stdout:
                        async for line in proc.stdout: 
                            decoded = line.decode('utf-8', errors='replace').rstrip()
                            self.log_task(Text.from_ansi(decoded), index)

                    await proc.wait()
                    rc = proc.returncode

                if rc == 0:
                    self.update_task_state(index, "success")
                    success_count += 1
                    self.log_main(f"[bold {THEME['success']}][OK][/] Process Complete.")
                    self.log_task(f"\n[bold {THEME['success']}]>>> EXECUTION SUCCESSFUL[/]", index)
                else:
                    if task.ignore_fail:
                        self.update_task_state(index, "skipped")
                        self.log_main(f"[bold {THEME['warning']}][WARN][/] Process failure (Code {rc}) suppressed by manifest.")
                        self.log_task(f"\n[bold {THEME['warning']}]>>> EXECUTION FAILED / SUPPRESSED (Code {rc})[/]", index)
                    else:
                        self.update_task_state(index, "failed")
                        fail_count += 1
                        self.log_main(f"[bold {THEME['error']}][FATAL][/] Process aborted execution sequence (Code {rc}).")
                        self.log_task(f"\n[bold {THEME['error']}]>>> FATAL EXECUTION FAILURE (Code {rc})[/]", index)
                        self.abort_flag = True

            except Exception as e:
                err_msg = f"[bold {THEME['error']}][ERROR][/] Internal Exception: {escape(str(e))}"
                self.log_main(err_msg); self.log_task(err_msg, index)
                self.update_task_state(index, "failed")
                if not task.ignore_fail: self.abort_flag = True

            await asyncio.sleep(0.01)

        self.log_main(f"\n[bold {THEME['accent']}]═══════ Pipeline Summary ═══════[/]")
        self.log_main(f"  Successful Deployments : [bold {THEME['success']}]{success_count}[/]")
        self.log_main(f"  Failed Operations      : [bold {THEME['error']}]{fail_count}[/]")
        
        if self.abort_flag:
            self.log_main(f"\n[bold {THEME['error']} blink]SYSTEM PIPELINE ABORTED.[/]")
        else:
            self.log_main(f"\n[bold {THEME['success']}]ARCHITECTURE DEPLOYMENT COMPLETED.[/]")

        self.log_main("\n[dim]Press 'Ctrl+C' or 'Q' to terminate abstraction shell.[/dim]")

    def action_quit(self) -> None:
        self.abort_flag = True
        self.exit()

if __name__ == "__main__":
    try:
        tasks = parse_manifest(UPDATE_SEQUENCE)
        has_sudo = SUDO_ALREADY_ACQUIRED
        
        if not has_sudo and any(t.mode == 'S' for t in tasks):
            if not verify_sudo(): sys.exit(1)
            has_sudo = True
            
        app = DuskyApp(tasks, has_sudo)
        app.run()
        
    except KeyboardInterrupt:
        sys.stdout.write("\n\033[1;33m[WARN]\033[0m User interrupt detected. Terminating.\n")
        sys.exit(130)
