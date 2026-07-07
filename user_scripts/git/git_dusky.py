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
import fnmatch
import subprocess
import readline
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
# Dictionary mapping the UI display string to a tuple of (new_path, old_path, status_code)
type PathMap = dict[str, tuple[str, str | None, str]]

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
        "env": git_env,
        "cwd": str(WORK_TREE)
    }
    if input_data is not None:
        kwargs["input"] = input_data

    proc = subprocess.run(cmd, **kwargs)
    
    if check and proc.returncode != 0:
        if capture and proc.stderr:
            console.print(f"[bold red]Git Internal Error:[/bold red]\n{proc.stderr.decode('utf-8', errors='replace').strip()}")
        raise subprocess.CalledProcessError(
            proc.returncode, cmd, output=proc.stdout, stderr=proc.stderr
        )
        
    return (
        proc.returncode, 
        proc.stdout.decode('utf-8', errors='replace') if proc.stdout else "", 
        proc.stderr.decode('utf-8', errors='replace') if proc.stderr else ""
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

# --- 3. PATHSPEC ORCHESTRATOR & FZF ---
def matches_pathspec(path: str | None, valid_paths: list[str]) -> bool:
    """Evaluates Git-style globs and exact boundaries purely in Python to avoid ARG_MAX issues."""
    if not path:
        return False
        
    # Explicitly ignore compiler cache folders/files
    if "__pycache__" in path or path.endswith((".pyc", ".pyo", ".pyd")):
        return False
        
    for vp in valid_paths:
        vp_clean = vp.rstrip("/")
        # Exact match or Directory prefix match
        if path == vp_clean or path.startswith(vp_clean + "/"):
            return True
        # Glob match (e.g., *.conf, **/*.sh)
        if fnmatch.fnmatch(path, vp_clean) or fnmatch.fnmatch(path, vp_clean + "/*"):
            return True
            
    return False

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
        stderr=subprocess.DEVNULL,
        cwd=str(WORK_TREE)
    )
    
    if proc.returncode not in (0, 130) and not proc.stdout:
        return []
        
    return [line for line in proc.stdout.decode('utf-8').split("\0") if line]

def get_list_pathspecs() -> list[str] | None:
    """Extracts valid paths ensuring boundary limitations to $HOME."""
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

# --- 4. EXECUTION PAYLOADS ---
def sync_all() -> None:
    """Smart-stages paths mathematically cross-referenced with fnmatch globs."""
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
        
    changed_paths: set[str] = set()
    unstaged_paths: set[str] = set()
    
    entries = status_out.split('\0')[:-1]
    it = iter(entries)
    
    for entry in it:
        if len(entry) < 3:
            continue
        status_code = entry[:2]
        path = entry[3:]
        
        orig_path = next(it, None) if "R" in status_code or "C" in status_code else None
            
        changed_paths.add(path)
        if orig_path:
            changed_paths.add(orig_path)

        # Stage only unstaged changes (avoids missing staged-deletion bounds crash)
        if status_code[1] != ' ':
            unstaged_paths.add(path)
            if orig_path:
                unstaged_paths.add(orig_path)

    paths_to_stage = [p for p in changed_paths if matches_pathspec(p, valid_paths)]
    paths_to_add = [p for p in unstaged_paths if matches_pathspec(p, valid_paths)]
                
    if not paths_to_stage:
        console.print("[bold green]✔[/bold green] Working tree immaculate (no matching files changed).")
        return

    if paths_to_add:
        payload = "\0".join(paths_to_add) + "\0"
        try:
            run_git(
                "add", 
                "--pathspec-from-file=-", 
                "--pathspec-file-nul",
                input_data=payload.encode('utf-8'),
                check=True,
                literal_pathspecs=True
            )
        except subprocess.CalledProcessError:
            console.print("[bold red]✖ Stage operation aborted due to Git bounds error.[/bold red]")
            return

    console.print("[bold green]✔[/bold green] Payload staged successfully:")
    for p in sorted(paths_to_stage):
        console.print(f"  [dim]➔ {p}[/dim]")
        
    commit_and_push(paths_to_stage)

def sync_single() -> None:
    """Interactive staging utilizing robust pattern mapping."""
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
        
        orig_path = next(it, None) if "R" in status_code or "C" in status_code else None
                
        if valid_paths is not None:
            # Block rendering if neither new nor old path matches defined tracked bounds
            if not (matches_pathspec(path, valid_paths) or matches_pathspec(orig_path, valid_paths)):
                continue

        # PEP 634 Structural Pattern Matching
        match status_code:
            case s if "R" in s or "C" in s:
                display = f"{status_code} {path} (from {orig_path})"
            case _:
                display = f"{status_code} {path}"
            
        path_map[display] = (path, orig_path, status_code)

    if not path_map:
        console.print("[bold yellow]⚠[/bold yellow] No changed files match .git_dusky_list.")
        return

    selected_lines = fzf_select(list(path_map.keys()), prompt="Stage Files (TAB to multi-select)", multi=True)
    if not selected_lines:
        return
    
    paths_to_stage: set[str] = set()
    paths_to_add: set[str] = set()
    
    # Flatten both new and old paths to ensure Git correctly registers atomic renames
    for line in selected_lines:
        if line in path_map:
            p, op, sc = path_map[line]
            paths_to_stage.add(p)
            if op:
                paths_to_stage.add(op)
            
            # Only stage if there are unstaged changes (Y is not ' ')
            if sc[1] != ' ':
                paths_to_add.add(p)
                if op:
                    paths_to_add.add(op)
                
    if paths_to_add:
        payload = "\0".join(paths_to_add) + "\0"
        try:
            run_git(
                "add", 
                "--pathspec-from-file=-", 
                "--pathspec-file-nul",
                input_data=payload.encode('utf-8'),
                check=True,
                literal_pathspecs=True
            )
            console.print("[bold green]✔[/bold green] Staged files successfully:")
            for p in sorted(paths_to_add):
                console.print(f"  [dim]➔ {p}[/dim]")
        except subprocess.CalledProcessError:
            console.print("[bold red]✖ Individual stage aborted due to Git error.[/bold red]")
            return
    else:
        console.print("[bold green]✔[/bold green] Selected files already staged:")
        for p in sorted(paths_to_stage):
            console.print(f"  [dim]➔ {p}[/dim]")
        
    commit_and_push(list(paths_to_stage))

def commit_and_push(files: list[str] | None = None, local_only: bool = False) -> None:
    """Atomic transaction logic enforcing strict ARG_MAX bounds via sets."""
    commit_files: list[str] | None = None
    
    if files:
        _, staged_out, _ = run_git("diff", "--cached", "--name-status", "-z")
        staged_entries = staged_out.split("\0")[:-1]
        
        staged_paths: set[str] = set()
        it = iter(staged_entries)
        for status in it:
            if not status:
                continue
            if status.startswith("R") or status.startswith("C"):
                src = next(it, None)
                dst = next(it, None)
                if src and dst:
                    if matches_pathspec(src, files) or matches_pathspec(dst, files):
                        staged_paths.add(src)
                        staged_paths.add(dst)
            else:
                path = next(it, None)
                if path:
                    if matches_pathspec(path, files):
                        staged_paths.add(path)
                        
        if not staged_paths:
            console.print("[bold yellow]⚠[/bold yellow] Index empty for specified files. Nothing to commit.")
            return
        commit_files = list(staged_paths)
    else:
        code, _, _ = run_git("diff", "--cached", "--quiet")
        if code == 0:
            console.print("[bold yellow]⚠[/bold yellow] Index empty. Nothing to commit.")
            return

    console.print("\n[bold cyan]Commit Message[/bold cyan]")
    msg = input(" ❯ ").strip()
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
    
    if local_only:
        console.print("[bold green]✔[/bold green] Committed changes locally.")
        return
        
    console.print("[bold cyan]Execute push to remote origin? (Y/n)[/bold cyan]")
    ans = input(" ❯ ").strip().lower()
    if not ans or ans in ("y", "yes"):
        console.print("[bold blue]Establishing connection...[/bold blue]")
        try:
            run_git("push", capture=False, check=True)
            console.print("[bold green]✔[/bold green] Synchronization successful.")
        except subprocess.CalledProcessError:
            console.print("[bold red]✖ Push failed. Resolve remote conflicts and try again.[/bold red]")

def discard_local_changes() -> None:
    """Discards all uncommitted changes (both staged and unstaged) in the working tree."""
    console.print(Panel(
        "[bold red]!!! DISCARD LOCAL CHANGES !!![/bold red]\n"
        "This will permanently erase all uncommitted modifications (both staged and unstaged) in the tracked files.",
        border_style="red"
    ))
    console.print("[bold red]Are you absolutely sure you want to discard all uncommitted changes? (y/N)[/bold red]")
    ans = input(" ❯ ").strip().lower()
    if ans in ("y", "yes"):
        try:
            run_git("reset", "--hard", "HEAD", capture=False, check=True)
            console.print("[bold green]✔[/bold green] Successfully discarded all local changes (hard reset to HEAD).")
        except subprocess.CalledProcessError:
            console.print("[bold red]✖ Reset operation failed.[/bold red]")

def quick_step_back() -> None:
    """Rolls back the repository by exactly 1 commit on both local and remote."""
    console.print(Panel(
        "[bold yellow]⚠ DELETE LAST COMMIT COMPLETELY (Local & Remote Reset) ⚠[/bold yellow]\n"
        "This will hard-reset the local repository to HEAD~1 and force-push to origin,\n"
        "permanently deleting the last commit from both local and remote history.",
        border_style="yellow"
    ))
    
    code, log_out, _ = run_git("log", "--format=%h", "-n", "2")
    if code != 0 or not log_out or len(log_out.splitlines()) < 2:
        console.print("[bold red]✖ Error:[/bold red] Cannot step back. Must have at least two commits in history.")
        return
        
    console.print("[bold red]Step back 1 commit on both local and remote? (y/N)[/bold red]")
    ans = input(" ❯ ").strip().lower()
    if ans in ("y", "yes"):
        try:
            run_git("reset", "--hard", "HEAD~1", capture=False, check=True)
            console.print("[bold green]✔[/bold green] Local repository reset to HEAD~1.")
            
            _, branch_out, _ = run_git("branch", "--show-current")
            branch_out = branch_out.strip()
            
            if not branch_out:
                console.print("[bold red]✖ Error: Detached HEAD state detected.[/bold red] Aborting remote push.")
                return
                
            console.print(f"[bold blue]Force-pushing to origin/{branch_out}...[/bold blue]")
            run_git("push", "origin", f"+{branch_out}", capture=False, check=True)
            console.print("[bold green]✔[/bold green] Step back 1 commit complete on remote.")
        except subprocess.CalledProcessError:
            console.print("[bold red]✖ Step back operation failed.[/bold red]")

def safe_revert_last_commit() -> None:
    """Safe non-destructive revert that appends a new commit undoing the last commit."""
    console.print(Panel(
        "[bold green]✔ UNDO LAST COMMIT SAFELY (Create Revert Commit) ✔[/bold green]\n"
        "This will create a new commit that undoes the changes of the last commit,\n"
        "preserving the commit history without rewriting it.",
        border_style="green"
    ))
    
    code, log_out, _ = run_git("log", "-n", "1")
    if code != 0 or not log_out:
        console.print("[bold red]✖ Error:[/bold red] No history found to revert.")
        return
        
    console.print("[bold cyan]Execute safe revert of the last commit? (Y/n)[/bold cyan]")
    ans = input(" ❯ ").strip().lower()
    if not ans or ans in ("y", "yes"):
        try:
            run_git("revert", "--no-edit", "HEAD", capture=False, check=True)
            console.print("[bold green]✔[/bold green] Safe revert commit created locally.")
            
            console.print("[bold cyan]Push the revert commit to remote? (Y/n)[/bold cyan]")
            ans_push = input(" ❯ ").strip().lower()
            if not ans_push or ans_push in ("y", "yes"):
                console.print("[bold blue]Pushing changes...[/bold blue]")
                run_git("push", capture=False, check=True)
                console.print("[bold green]✔[/bold green] Revert commit pushed successfully.")
        except subprocess.CalledProcessError:
            console.print("[bold red]✖ Safe revert operation aborted or failed.[/bold red]")

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
    
    console.print(f"[bold red]Execute HARD RESET to {commit_hash}? (Wipes local tracked changes) (y/N)[/bold red]")
    ans = input(" ❯ ").strip().lower()
    if ans in ("y", "yes"):
        try:
            run_git("reset", "--hard", commit_hash, capture=False, check=True)
            console.print(f"[bold green]✔[/bold green] Local state mathematically identical to {commit_hash}.")
            
            console.print("[bold red]FORCE PUSH to overwrite remote timeline? (y/N)[/bold red]")
            ans_push = input(" ❯ ").strip().lower()
            if ans_push in ("y", "yes"):
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
        table.add_column("Key", style="bold cyan", width=4, justify="right")
        table.add_column("Action", style="bold white")
        
        table.add_row("", "[bold cyan]⚡ DAILY WORKFLOWS[/bold cyan]")
        table.add_row("1", "Sync All (via .git_dusky_list)")
        table.add_row("2", "Sync Specific File(s)")
        table.add_row("3", "Commit Staged Changes (Local Only)")
        table.add_row("4", "Push Existing Local Commits")
        table.add_row("5", "View Delta Differential")
        
        table.add_row("", "")
        table.add_row("", "[bold yellow]🛡 RECOVERY & TIMELINE[/bold yellow]")
        table.add_row("6", "Undo Last Commit Safely (Creates new revert commit)")
        table.add_row("7", "Delete Last Commit Completely (Rewrites local & remote history)")
        table.add_row("8", "Discard All Uncommitted Local Changes")
        table.add_row("9", "Nuclear Revert (Local & Remote Sync)")
        
        table.add_row("", "")
        table.add_row("", "[bold magenta]⚙ ADVANCED SYSTEMS[/bold magenta]")
        table.add_row("10", "Engage Ephemeral Time Machine (TUI)")
        
        table.add_row("", "")
        table.add_row("q", "[bold red]Quit Dashboard[/bold red]")

        console.print(table)
        
        console.print("\n[bold blue]Awaiting Directive [1-10/q] [default: 1][/bold blue]")
        choice = input(" ❯ ").strip() or "1"
        while choice not in ("1", "2", "3", "4", "5", "6", "7", "8", "9", "10", "q"):
            console.print("[bold red]✖ Invalid choice. Please select a valid key.[/bold red]")
            choice = input(" ❯ ").strip() or "1"
        
        match choice:
            case "1": sync_all()
            case "2": sync_single()
            case "3": commit_and_push(local_only=True)
            case "4": 
                console.print("[bold blue]Establishing connection...[/bold blue]")
                try:
                    run_git("push", capture=False, check=True)
                except subprocess.CalledProcessError:
                    pass
            case "5": show_delta()
            case "6": safe_revert_last_commit()
            case "7": quick_step_back()
            case "8": discard_local_changes()
            case "9": nuclear_revert()
            case "10": run_time_machine()
            case "q": raise SystemExit(0)
            
        console.print("\n[dim]Press [Enter] to return to dashboard...[/dim]")
        input(" ❯ ")

if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        console.print("\n[bold yellow]⚠ Execution Terminated.[/bold yellow]")
        sys.exit(0)
