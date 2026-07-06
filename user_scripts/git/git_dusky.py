#!/usr/bin/env python3
"""
Dusky Dotfiles Manager - Obsidian Edition (Absolute Synchronous)
Architecture: Arch Linux / Hyprland / Wayland Context
Execution: Python 3.14 Strict Synchronous I/O
"""

import os
import sys
import shutil
import shlex
import subprocess
from pathlib import Path
from typing import Never

# Modern Rich UI components
from rich.console import Console
from rich.panel import Panel
from rich.prompt import Prompt, Confirm
from rich.table import Table

# --- 1. CONSTANTS, STATE & TYPE ALIASES (PEP 695) ---
HOME: Path = Path.home()
GIT_DIR: Path = HOME / "dusky"
WORK_TREE: Path = HOME
DOTFILES_LIST: Path = HOME / ".git_dusky_list"
TIME_MACHINE_BIN: Path = HOME / "user_scripts" / "git" / "time_machine" / "dusky_time_machine_tui.sh"

# Set Git environment variables globally so child processes (like fzf and sub-scripts)
# execute within the correct bare repository context.
os.environ["GIT_DIR"] = str(GIT_DIR)
os.environ["GIT_WORK_TREE"] = str(WORK_TREE)

console = Console()

type GitResult = tuple[int, str, str]
# Dictionary mapping the UI display string to a tuple of (new_path, old_path)
type PathMap = dict[str, tuple[str, str | None]]

# --- 2. SYNCHRONOUS GIT ENGINE ---
def run_git(
    *args: str, 
    capture: bool = True, 
    check: bool = False, 
    input_data: bytes | None = None,
    literal_pathspecs: bool = False
) -> GitResult:
    """Executes Git with strict standard I/O synchronization and environment isolation."""
    git_env = os.environ.copy()
    
    # Explicitly overriding ENV bounds guarantees Wayland context won't leak variables.
    git_env["GIT_WORK_TREE"] = str(WORK_TREE)
    git_env["GIT_DIR"] = str(GIT_DIR)
    
    if literal_pathspecs:
        git_env["GIT_LITERAL_PATHSPECS"] = "1"
    
    cmd = [
        "git",
        "--no-optional-locks",
        "--no-advice",
        *args
    ]
    
    kwargs = {
        "stdout": subprocess.PIPE if capture else None,
        "stderr": subprocess.PIPE if capture else None,
        "env": git_env
    }
    if input_data is not None:
        kwargs["input"] = input_data

    proc = subprocess.run(cmd, **kwargs)
    
    if check and proc.returncode != 0:
        if capture and proc.stderr:
            console.print(f"[bold red]Git Internal Error:[/bold red]\n{proc.stderr.decode('utf-8').strip()}")
        raise subprocess.CalledProcessError(
            proc.returncode, cmd, output=proc.stdout, stderr=proc.stderr
        )
        
    return (
        proc.returncode, 
        proc.stdout.decode('utf-8') if proc.stdout else "", 
        proc.stderr.decode('utf-8') if proc.stderr else ""
    )

def check_dependencies() -> None:
    """Validates the execution environment and required binaries."""
    for cmd in ("git", "fzf", "delta"):
        if not shutil.which(cmd):
            console.print(f"[bold red]✖ Error:[/bold red] '{cmd}' binary missing from $PATH.")
            sys.exit(1)
    
    if not GIT_DIR.is_dir():
        console.print(f"[bold red]✖ Error:[/bold red] Bare repository target missing: {GIT_DIR}")
        sys.exit(1)

# --- 3. FZF ORCHESTRATOR ---
def fzf_select(choices: list[str], prompt: str = "Select", multi: bool = False, preview: str | None = None) -> list[str]:
    """Feeds NUL-terminated strings to FZF safely via synchronous PIPEs."""
    if not choices:
        return []
    
    fzf_cmd = [
        "fzf",
        "--read0",
        "--print0",
        f"--prompt={prompt} ❯ ",
        "--height=50%",
        "--layout=reverse",
        "--border=rounded",
    ]
    if multi:
        fzf_cmd.append("--multi")
    if preview:
        fzf_cmd.extend(["--preview", preview])

    payload = "\0".join(choices) + "\0"
    
    proc = subprocess.run(
        fzf_cmd,
        input=payload.encode('utf-8'),
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL
    )
    
    # 130 is the standard SIGINT/ESC exit code for fzf.
    if proc.returncode not in (0, 130) and not proc.stdout:
        return []
        
    return [line for line in proc.stdout.decode('utf-8').split("\0") if line]

# --- 4. EXECUTION PAYLOADS ---
def get_list_pathspecs() -> list[str] | None:
    if not DOTFILES_LIST.is_file():
        return None
    raw_lines = DOTFILES_LIST.read_text(encoding="utf-8").splitlines()
    valid_paths: list[str] = []
    for line in raw_lines:
        clean = line.strip()
        if not clean or clean.startswith("#"):
            continue
        try:
            target = Path(clean).expanduser()
            if not target.is_absolute():
                target = WORK_TREE / target
            normalized_abs = Path(os.path.normpath(target))
            if normalized_abs.is_relative_to(WORK_TREE):
                rel_path = normalized_abs.relative_to(WORK_TREE)
                valid_paths.append(str(rel_path))
            else:
                console.print(f"[bold red]✖ Security Block:[/bold red] Path escaped work-tree -> {clean}")
        except ValueError:
            continue
    return valid_paths

def sync_all() -> None:
    """Smart-stages paths with strict lexical boundary checks."""
    valid_paths = get_list_pathspecs()
    
    if valid_paths is None:
        console.print(f"[bold yellow]⚠ Warn:[/bold yellow] {DOTFILES_LIST} missing. Executing blanket tracked update (-u).")
        try:
            run_git("add", "-u", check=True)
            console.print("[bold green]✔[/bold green] Blanket update successful.")
            commit_and_push()
        except subprocess.CalledProcessError:
            console.print("[bold red]✖ Blanket stage aborted due to Git error.[/bold red]")
        return
        
    if not valid_paths:
        console.print("[bold red]✖ Error:[/bold red] Zero valid file paths parsed.")
        return

    _, status_out, _ = run_git("status", "--porcelain=v1", "-z")
    if not status_out:
        console.print("[bold green]✔[/bold green] Working tree immaculate. No divergence detected.")
        return
        
    changed_paths = []
    entries = status_out.split('\0')[:-1]
    it = iter(entries)
    
    for entry in it:
        if len(entry) < 3:
            continue
        status_code = entry[:2]
        path = entry[3:]
        
        orig_path = None
        if "R" in status_code or "C" in status_code:
            orig_path = next(it, None)
            
        # Ensure both sides of a rename are captured if they fall within tracking bounds
        changed_paths.append(path)
        if orig_path:
            changed_paths.append(orig_path)

    paths_to_stage = []
    for cp in changed_paths:
        for vp in valid_paths:
            vp_clean = vp.rstrip("/")
            if cp == vp_clean or cp.startswith(vp_clean + "/"):
                paths_to_stage.append(cp)
                break
                
    if not paths_to_stage:
        console.print("[bold green]✔[/bold green] Working tree immaculate (no matching files changed).")
        return

    payload = "\0".join(paths_to_stage) + "\0"
    
    try:
        run_git(
            "add", 
            "--pathspec-from-file=-", 
            "--pathspec-file-nul",
            input_data=payload.encode('utf-8'),
            check=True,
            literal_pathspecs=True
        )
        
        console.print("[bold green]✔[/bold green] Payload staged successfully.")
        commit_and_push(paths_to_stage)
    except subprocess.CalledProcessError:
        console.print("[bold red]✖ Stage operation aborted due to Git bounds error.[/bold red]")

def sync_single() -> None:
    """Interactive staging utilizing structural pattern mapping."""
    _, status_out, _ = run_git("status", "--porcelain=v1", "-z")
    if not status_out:
        console.print("[bold green]✔[/bold green] Working tree immaculate. No divergence detected.")
        return

    valid_paths = get_list_pathspecs()
    entries = status_out.split('\0')[:-1]
    
    path_map: PathMap = {}
    it = iter(entries)
    
    for entry in it:
        if len(entry) < 3:
            continue
            
        status_code = entry[:2]
        path = entry[3:]
        
        orig_path = None
        # PEP 634 Pattern Matching
        match status_code:
            case s if "R" in s or "C" in s:
                orig_path = next(it, None)
            case _:
                pass
                
        if valid_paths is not None:
            matched = False
            # We must verify if EITHER side of a rename matches the tracking list
            paths_to_check = [path]
            if orig_path:
                paths_to_check.append(orig_path)
                
            for ptc in paths_to_check:
                for vp in valid_paths:
                    vp_clean = vp.rstrip("/")
                    if ptc == vp_clean or ptc.startswith(vp_clean + "/"):
                        matched = True
                        break
                if matched:
                    break
                    
            if not matched:
                continue

        if orig_path:
            display = f"{status_code} {path} (from {orig_path})"
        else:
            display = f"{status_code} {path}"
            
        path_map[display] = (path, orig_path)

    if not path_map:
        console.print("[bold yellow]⚠[/bold yellow] No changed files match .git_dusky_list.")
        return

    selected_lines = fzf_select(list(path_map.keys()), prompt="Stage Files (TAB to multi-select)", multi=True)
    if not selected_lines:
        return
    
    # Flatten both new and old paths to ensure Git correctly registers atomic renames
    paths_to_stage = []
    for line in selected_lines:
        if line in path_map:
            p, op = path_map[line]
            paths_to_stage.append(p)
            if op:
                paths_to_stage.append(op)
                
    payload = "\0".join(paths_to_stage) + "\0"
    
    try:
        run_git(
            "add", 
            "--pathspec-from-file=-", 
            "--pathspec-file-nul",
            input_data=payload.encode('utf-8'),
            check=True,
            literal_pathspecs=True
        )
        console.print(f"[bold green]✔[/bold green] Staged files successfully.")
        commit_and_push(paths_to_stage)
    except subprocess.CalledProcessError:
        console.print("[bold red]✖ Individual stage aborted due to Git error.[/bold red]")

def commit_and_push(files: list[str] | None = None) -> None:
    """Atomic commit and push transaction logic enforcing strict ARG_MAX safety."""
    commit_files: list[str] | None = None
    
    if files:
        _, staged_out, _ = run_git("diff", "--cached", "--name-only", "-z")
        staged_list = [f for f in staged_out.split("\0") if f]
        
        matched_files = []
        for f in staged_list:
            for spec in files:
                clean_spec = spec.rstrip("/")
                if f == clean_spec or f.startswith(clean_spec + "/"):
                    matched_files.append(f)
                    break
        
        if not matched_files:
            console.print("[bold yellow]⚠[/bold yellow] Index empty for specified files. Nothing to commit.")
            return
        commit_files = matched_files
    else:
        code, _, _ = run_git("diff", "--cached", "--quiet")
        if code == 0:
            console.print("[bold yellow]⚠[/bold yellow] Index empty. Nothing to commit.")
            return

    msg = Prompt.ask("\n[bold cyan]Commit Message[/bold cyan]").strip()
    if not msg:
        console.print("[bold red]✖ Aborted:[/bold red] Commit message cannot be empty or whitespace.")
        return

    try:
        commit_args = ["commit"]
        payload = None
        
        # Flawlessly routes `--only` via stdin payloads to prevent ARG_MAX kernel crashes
        if commit_files:
            commit_args.append("--only")
            commit_args.extend(["--pathspec-from-file=-", "--pathspec-file-nul"])
            payload = ("\0".join(commit_files) + "\0").encode('utf-8')
            
        commit_args.extend(["-m", msg])
            
        run_git(*commit_args, input_data=payload, check=True, literal_pathspecs=True)
    except subprocess.CalledProcessError:
        console.print("[bold red]✖ Commit failed (Hooks/Formatting block).[/bold red]")
        return
    
    if Confirm.ask("[bold cyan]Execute push to remote origin?[/bold cyan]", default=True):
        console.print("[bold blue]Establishing connection...[/bold blue]")
        try:
            run_git("push", capture=False, check=True)
            console.print("[bold green]✔[/bold green] Synchronization successful.")
        except subprocess.CalledProcessError:
            console.print("[bold red]✖ Push failed. Resolve remote conflicts and try again.[/bold red]")

def show_delta() -> None:
    """Pipes differential directly through Delta."""
    console.print("[bold blue]Executing Delta differential...[/bold blue]")
    run_git("-c", "core.pager=delta", "diff", "HEAD", capture=False)

def nuclear_revert() -> None:
    """Absolute destructive timeline sync. Hard resets local tree and force-pushes."""
    console.print(Panel(
        "[bold red]!!! TIMELINE OBLITERATION !!![/bold red]\n"
        "This will permanently erase local tracking changes and physically overwrite the GitHub remote.\n"
        "This operation matches the remote strictly to HEAD.",
        border_style="red"
    ))
    
    _, log_out, _ = run_git("log", "--format=%h %s", "-n", "30")
    if not log_out:
        return
        
    commits = log_out.splitlines()
    preview_cmd = "git --no-advice show --color=always {1}"
    
    target = fzf_select(commits, prompt="Select Anchor Commit", preview=preview_cmd)
    if not target:
        return
        
    commit_hash = target[0].split()[0]
    console.print(f"\n[bold yellow]Target Anchor:[/bold yellow] {target[0]}")
    
    if Confirm.ask(f"[bold red]Execute HARD RESET to {commit_hash}? (Wipes local tracked changes)[/bold red]", default=False):
        try:
            run_git("reset", "--hard", commit_hash, capture=False, check=True)
            console.print(f"[bold green]✔[/bold green] Local state mathematically identical to {commit_hash}.")
            
            if Confirm.ask("[bold red]FORCE PUSH to overwrite remote timeline?[/bold red]", default=False):
                _, branch_out, _ = run_git("branch", "--show-current")
                branch_out = branch_out.strip()
                
                if not branch_out:
                    console.print("[bold red]✖ Error: Detached HEAD state detected.[/bold red] Aborting force push.")
                    return
                    
                run_git("push", "origin", f"+{branch_out}", capture=False, check=True)
                console.print(f"[bold green]✔[/bold green] Remote repository obliteration complete.")
        except subprocess.CalledProcessError:
            console.print("[bold red]✖ Nuclear operation interrupted by fatal error.[/bold red]")

def run_time_machine() -> None:
    """Handoff execution to the highly-optimized Ephemeral Bash TUI."""
    if TIME_MACHINE_BIN.is_file() and os.access(TIME_MACHINE_BIN, os.X_OK):
        console.print("[bold blue]Engaging ZRAM Ephemeral Time Machine...[/bold blue]")
        subprocess.run([str(TIME_MACHINE_BIN)])
    else:
        console.print(f"[bold red]✖ Error:[/bold red] Time machine binary not found or not executable at {TIME_MACHINE_BIN}")

# --- 5. MAIN ROUTING ENGINE ---
def main() -> Never:
    check_dependencies()
    
    while True:
        console.clear()
        table = Table(title="󰏖 Dusky Dotfiles Manager", show_header=False, box=None, title_style="bold blue")
        table.add_column("Key", style="bold cyan")
        table.add_column("Action", style="bold white")
        
        table.add_row("1", "Sync All (via .git_dusky_list)")
        table.add_row("2", "Sync Specific File(s)")
        table.add_row("3", "View Delta Differential")
        table.add_row("4", "Push Existing Local Commits")
        table.add_row("5", "Nuclear Revert (Local & Remote Sync)")
        table.add_row("6", "Engage Ephemeral Time Machine (TUI)")
        table.add_row("q", "Quit Dashboard")

        console.print(table)
        
        choice = Prompt.ask("\n[bold blue]Awaiting Directive[/bold blue]", choices=["1", "2", "3", "4", "5", "6", "q"], default="1", show_default=False)
        
        match choice:
            case "1": sync_all()
            case "2": sync_single()
            case "3": show_delta()
            case "4": 
                console.print("[bold blue]Establishing connection...[/bold blue]")
                try:
                    run_git("push", capture=False, check=True)
                except subprocess.CalledProcessError:
                    pass
            case "5": nuclear_revert()
            case "6": run_time_machine()
            case "q": raise SystemExit(0)
            
        Prompt.ask("\n[dim]Press [Enter] to return to dashboard...[/dim]", default="")

if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        console.print("\n[bold yellow]⚠ Execution Terminated.[/bold yellow]")
        sys.exit(0)
