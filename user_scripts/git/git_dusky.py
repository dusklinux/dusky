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
from rich import box
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
def sync_all(local_only: bool = False) -> None:
    """Smart-stages paths mathematically cross-referenced with fnmatch globs."""
    valid_paths = get_list_pathspecs()
    
    if valid_paths is None:
        console.print(f"[bold yellow]⚠ Warn:[/bold yellow] {DOTFILES_LIST} missing. Executing blanket tracked update (-u).")
        try:
            run_git("add", "-u", check=True)
            console.print("[bold green]✔[/bold green] Blanket update successful.")
            commit_and_push(local_only=local_only)
        except subprocess.CalledProcessError:
            console.print("[bold red]✖ Blanket stage aborted due to Git error.[/bold red]")
        return
        
    if not valid_paths:
        console.print("[bold red]✖ Error:[/bold red] Zero valid file paths parsed.")
        return

    _, status_out, _ = run_git("status", "--porcelain=v1", "-z", "-u", "--", *valid_paths)
    if not status_out:
        console.print("[bold green]✔[/bold green] Working tree immaculate. No divergence detected.")
        return
        
    changed_paths: set[str] = set()
    unstaged_paths: set[str] = set()
    paths_to_remove: set[str] = set()
    
    entries = status_out.split('\0')[:-1]
    it = iter(entries)
    
    for entry in it:
        if len(entry) < 3:
            continue
        status_code = entry[:2]
        path = entry[3:]
        
        orig_path = next(it, None) if "R" in status_code or "C" in status_code else None
            
        full_path = WORK_TREE / path
        exists = full_path.exists() or full_path.is_symlink()
        
        if exists:
            changed_paths.add(path)
            if orig_path:
                changed_paths.add(orig_path)
            # Stage only unstaged changes (avoids missing staged-deletion bounds crash)
            if status_code[1] != ' ':
                unstaged_paths.add(path)
                if orig_path:
                    unstaged_paths.add(orig_path)
        else:
            # File is deleted on disk
            if status_code[0] == 'A':
                # Untracked staged file, now deleted on disk -> unstage/remove from index
                paths_to_remove.add(path)
            else:
                # Tracked file, deleted on disk -> stage deletion and commit it
                changed_paths.add(path)
                paths_to_remove.add(path)
                if orig_path:
                    changed_paths.add(orig_path)
                    paths_to_remove.add(orig_path)

    paths_to_stage = [p for p in changed_paths if matches_pathspec(p, valid_paths)]
    paths_to_add = [p for p in unstaged_paths if matches_pathspec(p, valid_paths)]
    paths_to_rm = [p for p in paths_to_remove if matches_pathspec(p, valid_paths)]
                
    if not paths_to_stage and not paths_to_rm:
        console.print("[bold green]✔[/bold green] Working tree immaculate (no matching files changed).")
        return

    if paths_to_rm:
        payload = "\0".join(paths_to_rm) + "\0"
        try:
            run_git(
                "rm", "--cached", "-r", "--ignore-unmatch",
                "--pathspec-from-file=-",
                "--pathspec-file-nul",
                input_data=payload.encode('utf-8'),
                check=True,
                literal_pathspecs=True
            )
        except subprocess.CalledProcessError:
            pass

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

    if not paths_to_stage:
        console.print("[bold green]✔[/bold green] Index synchronized. No changes left to commit.")
        return

    console.print("[bold green]✔[/bold green] Payload staged successfully:")
    for p in sorted(paths_to_stage):
        console.print(f"  [dim]➔ {p}[/dim]")
        
    commit_and_push(paths_to_stage, local_only=local_only)

def sync_single() -> None:
    """Interactive staging utilizing robust pattern mapping."""
    valid_paths = get_list_pathspecs()
    status_args = ["status", "--porcelain=v1", "-z", "-u"]
    if valid_paths is not None:
        if not valid_paths:
            console.print("[bold red]✖ Error:[/bold red] Zero valid file paths parsed.")
            return
        status_args += ["--"] + valid_paths

    _, status_out, _ = run_git(*status_args)
    if not status_out:
        console.print("[bold green]✔[/bold green] Working tree immaculate. No divergence detected.")
        return

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
    paths_to_rm: set[str] = set()
    
    # Flatten both new and old paths to ensure Git correctly registers atomic renames
    for line in selected_lines:
        if line in path_map:
            p, op, sc = path_map[line]
            
            full_path = WORK_TREE / p
            exists = full_path.exists() or full_path.is_symlink()
            
            if exists:
                paths_to_stage.add(p)
                if op:
                    paths_to_stage.add(op)
                
                # Only stage if there are unstaged changes (Y is not ' ')
                if sc[1] != ' ':
                    paths_to_add.add(p)
                    if op:
                        paths_to_add.add(op)
            else:
                # File does not exist on disk
                if sc[0] == 'A':
                    paths_to_rm.add(p)
                else:
                    paths_to_stage.add(p)
                    paths_to_rm.add(p)
                    if op:
                        paths_to_stage.add(op)
                        paths_to_rm.add(op)

    if paths_to_rm:
        payload = "\0".join(paths_to_rm) + "\0"
        try:
            run_git(
                "rm", "--cached", "-r", "--ignore-unmatch",
                "--pathspec-from-file=-",
                "--pathspec-file-nul",
                input_data=payload.encode('utf-8'),
                check=True,
                literal_pathspecs=True
            )
            console.print("[bold green]✔[/bold green] Removed/Unstaged files successfully:")
            for p in sorted(paths_to_rm):
                console.print(f"  [dim]➔ {p}[/dim]")
        except subprocess.CalledProcessError:
            pass

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
        if paths_to_stage:
            console.print("[bold green]✔[/bold green] Selected files already staged:")
            for p in sorted(paths_to_stage):
                console.print(f"  [dim]➔ {p}[/dim]")
        
    if paths_to_stage:
        commit_and_push(list(paths_to_stage))
    else:
        console.print("[bold green]✔[/bold green] Index synchronized. No changes left to commit.")

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

    console.print("\n[bold cyan]Commit Message (or type 'abort' to cancel)[/bold cyan]")
    while True:
        msg = input(" ❯ ").strip()
        if not msg:
            console.print("[bold red]✖ Error: Commit message cannot be empty.[/bold red]")
            continue
        if msg.lower() in ("abort", "q"):
            console.print("[bold yellow]⚠ Aborted: Commit cancelled by user.[/bold yellow]")
            return
        break

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
    valid_paths = get_list_pathspecs()
    if valid_paths is None:
        status_args = ["status", "--porcelain=v1", "-z", "-u"]
    else:
        status_args = ["status", "--porcelain=v1", "-z", "-u", "--"] + valid_paths

    code, status_out, _ = run_git(*status_args)
    if code != 0:
        console.print("[bold red]✖ Error: Failed to retrieve repository status.[/bold red]")
        return
        
    entries = status_out.split('\0')[:-1]
    changed_tracked: list[str] = []
    untracked_to_delete: list[str] = []
    it = iter(entries)
    
    for entry in it:
        if len(entry) < 3:
            continue
        status_code = entry[:2]
        path = entry[3:]
        
        orig_path = next(it, None) if "R" in status_code or "C" in status_code else None
        
        if status_code == "??" or status_code == "??":
            untracked_to_delete.append(path)
        else:
            display = f"➔ {path}"
            if orig_path:
                display += f" (from {orig_path})"
            changed_tracked.append(display)

    if not changed_tracked and not untracked_to_delete:
        console.print("[bold green]✔[/bold green] Working tree already clean. No changes to discard.")
        return

    console.print(Panel(
        "[bold red]!!! DISCARD LOCAL CHANGES !!![/bold red]\n"
        "This will permanently erase local changes of your choice (modifications in tracked files and/or untracked files).",
        border_style="red"
    ))
    
    if changed_tracked:
        console.print("\n[bold yellow]The following modified/deleted files can be REVERTED:[/bold yellow]")
        for item in sorted(changed_tracked):
            console.print(f"  [red]{item}[/red]")
            
    if untracked_to_delete:
        console.print("\n[bold yellow]The following untracked files can be PERMANENTLY DELETED:[/bold yellow]")
        for item in sorted(untracked_to_delete):
            console.print(f"  [red]➔ {item}[/red]")
    console.print()

    revert_tracked = False
    delete_untracked = False

    if changed_tracked:
        console.print("[bold cyan]Revert all modifications in tracked files? (y/N)[/bold cyan]")
        ans = input(" ❯ ").strip().lower()
        if ans in ("y", "yes"):
            revert_tracked = True

    if untracked_to_delete:
        console.print("[bold red]Permanently delete all listed untracked files? (y/N)[/bold red]")
        ans = input(" ❯ ").strip().lower()
        if ans in ("y", "yes"):
            delete_untracked = True

    if not revert_tracked and not delete_untracked:
        console.print("[bold yellow]⚠ Aborted: No changes were discarded.[/bold yellow]")
        return

    try:
        if revert_tracked:
            # 1. Reset tracked files if HEAD exists
            code_head, _, _ = run_git("rev-parse", "--verify", "HEAD")
            if code_head == 0:
                run_git("reset", "--hard", "HEAD", capture=False, check=True)
            console.print("[bold green]✔[/bold green] Tracked files successfully reverted.")
            
        if delete_untracked:
            # 2. Safely delete listed untracked files
            for path in untracked_to_delete:
                full_path = WORK_TREE / path
                if full_path.is_file() or full_path.is_symlink():
                    full_path.unlink()
                elif full_path.is_dir():
                    shutil.rmtree(full_path)
            console.print("[bold green]✔[/bold green] Untracked files successfully deleted.")
    except subprocess.CalledProcessError:
        console.print("[bold red]✖ Operation failed.[/bold red]")

def reset_local_to_remote() -> None:
    """Hard resets the local repository to match the remote branch tracking state."""
    console.print(Panel(
        "[bold red]⚠ RESET LOCAL STATE TO MATCH GITHUB ⚠[/bold red]\n"
        "This will discard all local commits that haven't been pushed to GitHub\n"
        "AND erase all uncommitted edits on your disk, resetting everything to match the remote.",
        border_style="red"
    ))
    
    # Get current branch name
    _, branch_out, _ = run_git("branch", "--show-current")
    branch_out = branch_out.strip()
    if not branch_out:
        console.print("[bold red]✖ Error: Detached HEAD state detected.[/bold red]")
        return
        
    console.print(f"[bold red]Reset local state and overwrite all files to match origin/{branch_out}? (y/N)[/bold red]")
    ans = input(" ❯ ").strip().lower()
    if ans in ("y", "yes"):
        try:
            console.print("[bold blue]Fetching latest state from GitHub...[/bold blue]")
            # Ensure remote origin has the correct fetch refspec
            run_git("config", "remote.origin.fetch", "+refs/heads/*:refs/remotes/origin/*")
            run_git("fetch", "origin", capture=False, check=True)
            
            console.print(f"[bold blue]Hard resetting to origin/{branch_out}...[/bold blue]")
            run_git("reset", "--hard", f"origin/{branch_out}", capture=False, check=True)
            console.print("[bold green]✔[/bold green] Local state successfully synced with GitHub remote.")
        except subprocess.CalledProcessError:
            console.print("[bold red]✖ Sync operation failed.[/bold red]")

def quick_step_back() -> None:
    """Rolls back the repository by exactly 1 commit on both local and remote."""
    console.print(Panel(
        "[bold red]⚠ DELETE LAST COMMIT FROM REMOTE ⚠[/bold red]\n"
        "This will hard-reset the local repository to HEAD~1 and force-push to origin,\n"
        "permanently deleting the last commit from both local and remote history.",
        border_style="red"
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

def undo_local_commits_to_commit() -> None:
    """Safe mixed reset to a selected past commit (uncommits files, keeping disk modifications)."""
    console.print(Panel(
        "[bold yellow]⚠ UNDO LOCAL COMMITS TO A SPECIFIC COMMIT ⚠[/bold yellow]\n"
        "This will reset your local HEAD to a selected past commit,\n"
        "returning all files changed since that commit to your unstaged area.\n"
        "All edits on disk will be safely preserved.",
        border_style="yellow"
    ))
    
    _, log_out, _ = run_git("log", "--format=%h %s", "-n", "30")
    if not log_out:
        console.print("[bold red]✖ Error:[/bold red] No commit history found.")
        return
        
    commits = log_out.splitlines()
    preview_cmd = "git --no-advice show --color=always {1}"
    
    target = fzf_select(commits, prompt="Select Target Commit", preview=preview_cmd)
    if not target:
        return
        
    commit_hash = target[0].split()[0]
    console.print(f"\n[bold yellow]Target Commit:[/bold yellow] {target[0]}")
    
    console.print(f"[bold cyan]Reset local HEAD to {commit_hash} and preserve edits? (y/N)[/bold cyan]")
    ans = input(" ❯ ").strip().lower()
    if ans in ("y", "yes"):
        try:
            run_git("reset", commit_hash, capture=False, check=True)
            console.print(f"[bold green]✔[/bold green] Local HEAD reset to {commit_hash}. Changes preserved in working tree.")
        except subprocess.CalledProcessError:
            console.print("[bold red]✖ Reset operation failed.[/bold red]")

def delete_local_commits_to_commit() -> None:
    """Destructive hard reset to a selected past commit (uncommits files and wipes edits)."""
    console.print(Panel(
        "[bold red]⚠ DELETE LOCAL COMMITS SINCE A SPECIFIC COMMIT ⚠[/bold red]\n"
        "This will permanently delete commits from your local history up to the selected past commit,\n"
        "AND erase all changes associated with those commits from your disk.",
        border_style="red"
    ))
    
    _, log_out, _ = run_git("log", "--format=%h %s", "-n", "30")
    if not log_out:
        console.print("[bold red]✖ Error:[/bold red] No commit history found.")
        return
        
    commits = log_out.splitlines()
    preview_cmd = "git --no-advice show --color=always {1}"
    
    target = fzf_select(commits, prompt="Select Target Commit", preview=preview_cmd)
    if not target:
        return
        
    commit_hash = target[0].split()[0]
    console.print(f"\n[bold yellow]Target Commit:[/bold yellow] {target[0]}")
    
    console.print(f"[bold red]Delete all local commits since {commit_hash} and discard all their edits? (y/N)[/bold red]")
    ans = input(" ❯ ").strip().lower()
    if ans in ("y", "yes"):
        try:
            run_git("reset", "--hard", commit_hash, capture=False, check=True)
            console.print(f"[bold green]✔[/bold green] Local state reset to {commit_hash}. Changes discarded.")
        except subprocess.CalledProcessError:
            console.print("[bold red]✖ Reset operation failed.[/bold red]")

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
        "[bold red]⚠ DELETE COMMITS FROM REMOTE ⚠[/bold red]\n"
        "This will permanently delete commits since the selected commit from local history\n"
        "AND force-push to overwrite the remote history on GitHub.",
        border_style="red"
    ))
    
    _, log_out, _ = run_git("log", "--format=%h %s", "-n", "30")
    if not log_out:
        return
        
    commits = log_out.splitlines()
    preview_cmd = "git --no-advice show --color=always {1}"
    
    target = fzf_select(commits, prompt="Select Target Commit", preview=preview_cmd)
    if not target:
        return
        
    commit_hash = target[0].split()[0]
    console.print(f"\n[bold yellow]Target Commit:[/bold yellow] {target[0]}")
    
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
            console.print("[bold red]✖ Force reset operation interrupted by error.[/bold red]")

def run_time_machine() -> None:
    """Handoff execution to the highly-optimized Ephemeral Bash TUI."""
    if TIME_MACHINE_BIN.is_file() and os.access(TIME_MACHINE_BIN, os.X_OK):
        console.print("[bold blue]Engaging ZRAM Ephemeral Time Machine...[/bold blue]")
        subprocess.run([str(TIME_MACHINE_BIN)])
    else:
        console.print(f"[bold red]✖ Error:[/bold red] Time machine binary not found or not executable at {TIME_MACHINE_BIN}")

# --- 5. MAIN ROUTING ENGINE ---
def print_help() -> None:
    """Prints a categorized, color-coded usage manual of CLI quick flags."""
    console.print("\n[bold blue]󰏖 Dusky CLI Quick Help[/bold blue]")
    console.print("Usage: [bold green]dusky [option][/bold green]")
    console.print("If no option is provided, the interactive dashboard is opened.\n")
    
    table = Table(box=box.ROUNDED, show_header=True, header_style="bold white")
    table.add_column("Option", style="bold", width=8, justify="center")
    table.add_column("Action", style="white")
    table.add_column("Destructive", justify="center")
    
    # 1. Working Tree & Status (Cyan)
    table.add_row("[bold cyan]Category[/bold cyan]", "[bold cyan]  WORKING TREE & STATUS[/bold cyan]", "")
    table.add_row("1", "[cyan]Commit All (Local & Remote)[/cyan]", "[green]No[/green]")
    table.add_row("2", "[cyan]Commit Specific File(s) (Local & Remote)[/cyan]", "[green]No[/green]")
    table.add_row("5", "[cyan]View Delta Differential[/cyan]", "[green]No[/green]")
    table.add_section()
    
    # 2. Commits & Sync (Green)
    table.add_row("[bold green]Category[/bold green]", "[bold green]  COMMITS & SYNC[/bold green]", "")
    table.add_row("3", "[green]Commit All (Local Only)[/green]", "[green]No[/green]")
    table.add_row("4", "[green]Push Existing Local Commits to Remote[/green]", "[green]No[/green]")
    table.add_section()
    
    # 3. Local History Rollback (Yellow)
    table.add_row("[bold yellow]Category[/bold yellow]", "[bold yellow]  LOCAL HISTORY ROLLBACK[/bold yellow]", "")
    table.add_row("7", "[yellow]Undo Local Commits to a Specific Commit[/yellow]", "[green]No[/green]")
    table.add_row("8", "[yellow]Delete Local Commits since a Specific Commit[/yellow]", "[bold red]YES[/bold red]")
    table.add_row("10", "[yellow]Discard All Uncommitted Local Changes[/yellow]", "[bold red]YES[/bold red]")
    table.add_row("11", "[yellow]Reset Local State to Match GitHub[/yellow]", "[bold red]YES[/bold red]")
    table.add_section()
    
    # 4. Force Rewriting (Red)
    table.add_row("[bold red]Category[/bold red]", "[bold red]  FORCE REWRITING[/bold red]", "")
    table.add_row("6", "[red]Undo Last Commit Safely (Create Revert Commit)[/red]", "[green]No[/green]")
    table.add_row("9", "[red]Delete Last Commit from Remote[/red]", "[bold red]YES[/bold red]")
    table.add_row("12", "[red]Delete Commits since a Specific Commit from Remote[/red]", "[bold red]YES[/bold red]")
    table.add_section()
    
    # 5. Advanced Toolbox (Magenta)
    table.add_row("[bold magenta]Category[/bold magenta]", "[bold magenta]  ADVANCED TOOLBOX[/bold magenta]", "")
    table.add_row("13", "[magenta]Engage Ephemeral Time Machine (TUI)[/magenta]", "[green]No[/green]")
    table.add_row("q", "[magenta]Quit Dashboard[/magenta]", "[green]No[/green]")
    table.add_row("h", "[magenta]Show this CLI help menu[/magenta]", "[green]No[/green]")
    
    console.print(table)

def main() -> Never:
    check_dependencies()
    
    # CLI Quick Routing
    if len(sys.argv) > 1:
        choice = sys.argv[1].strip()
        if choice in ("-h", "--help", "help", "h"):
            print_help()
            sys.exit(0)
        elif choice in ("1", "2", "3", "4", "5", "6", "7", "8", "9", "10", "11", "12", "13", "q"):
            match choice:
                case "1": sync_all()
                case "2": sync_single()
                case "3": sync_all(local_only=True)
                case "4": 
                    console.print("[bold blue]Establishing connection...[/bold blue]")
                    try:
                        run_git("push", capture=False, check=True)
                    except subprocess.CalledProcessError:
                        pass
                case "5": show_delta()
                case "6": safe_revert_last_commit()
                case "7": undo_local_commits_to_commit()
                case "8": delete_local_commits_to_commit()
                case "9": quick_step_back()
                case "10": discard_local_changes()
                case "11": reset_local_to_remote()
                case "12": nuclear_revert()
                case "13": run_time_machine()
                case "q": sys.exit(0)
            sys.exit(0)
        else:
            console.print(f"[bold red]✖ Invalid choice argument '{choice}'.[/bold red]")
            print_help()
            sys.exit(1)
            
    while True:
        console.clear()
        console.print("[bold blue]󰏖 Dusky Dotfiles Manager[/bold blue]\n")
        
        # 1. Working Tree & Staging (Cyan)
        console.print(Panel(
            "[bold cyan]1[/bold cyan] │ Commit All (Local & Remote)\n"
            "[bold cyan]2[/bold cyan] │ Commit Specific File(s) (Local & Remote)\n"
            "[bold cyan]5[/bold cyan] │ View Delta Differential",
            title="[bold cyan]  WORKING TREE & STATUS (Local File Operations)[/bold cyan]",
            border_style="cyan",
            title_align="left",
            box=box.ROUNDED
        ))
        
        # 2. Commits & Sync (Green)
        console.print(Panel(
            "[bold green]3[/bold green] │ Commit All (Local Only)\n"
            "[bold green]4[/bold green] │ Push Existing Local Commits to Remote",
            title="[bold green]  COMMITS & SYNC (Save to Local / Remote)[/bold green]",
            border_style="green",
            title_align="left",
            box=box.ROUNDED
        ))
        
        # 3. Local History Rollback (Yellow)
        console.print(Panel(
            "[bold yellow]7[/bold yellow]  │ Undo Local Commits to a Specific Commit (Safe - uncommits but keeps edits on disk)\n"
            "[bold yellow]8[/bold yellow]  │ [bold red]Delete[/bold red] Local Commits since a Specific Commit (Destructive - discards all edits)\n"
            "[bold yellow]10[/bold yellow] │ [bold red]Discard[/bold red] All Uncommitted Local Changes (Destructive - wipes unstaged edits)\n"
            "[bold yellow]11[/bold yellow] │ [bold red]Reset[/bold red] Local State to Match GitHub (Destructive - discards local commits & edits)",
            title="[bold yellow]  LOCAL HISTORY ROLLBACK (Changes Stay on Local PC Only)[/bold yellow]",
            border_style="yellow",
            title_align="left",
            box=box.ROUNDED
        ))
        
        # 4. Force Rewriting (Red)
        console.print(Panel(
            "[bold red]6[/bold red]  │ Undo Last Commit Safely (Creates new revert commit on Local & Remote)\n"
            "[bold red]9[/bold red]  │ [bold red]Delete[/bold red] Last Commit from Remote (Destructive - rewrites local & remote)\n"
            "[bold red]12[/bold red] │ [bold red]Delete[/bold red] Commits since a Specific Commit from Remote (Destructive - rewrites local & remote)",
            title="[bold red]  FORCE REWRITING (Alters Both Local & GitHub Remote History)[/bold red]",
            border_style="red",
            title_align="left",
            box=box.ROUNDED
        ))
        
        # 5. Advanced Toolbox (Magenta)
        console.print(Panel(
            "[bold magenta]13[/bold magenta] │ Engage Ephemeral Time Machine (TUI)\n"
            "[bold red]q[/bold red]  │ Quit Dashboard",
            title="[bold magenta]  ADVANCED TOOLBOX[/bold magenta]",
            border_style="magenta",
            title_align="left",
            box=box.ROUNDED
        ))
        
        console.print("\n[bold blue]Awaiting Directive [1-13/q] [default: 1][/bold blue]")
        choice = input(" ❯ ").strip() or "1"
        while choice not in ("1", "2", "3", "4", "5", "6", "7", "8", "9", "10", "11", "12", "13", "q"):
            console.print("[bold red]✖ Invalid choice. Please select a valid key.[/bold red]")
            choice = input(" ❯ ").strip() or "1"
        
        match choice:
            case "1": sync_all()
            case "2": sync_single()
            case "3": sync_all(local_only=True)
            case "4": 
                console.print("[bold blue]Establishing connection...[/bold blue]")
                try:
                    run_git("push", capture=False, check=True)
                except subprocess.CalledProcessError:
                    pass
            case "5": show_delta()
            case "6": safe_revert_last_commit()
            case "7": undo_local_commits_to_commit()
            case "8": delete_local_commits_to_commit()
            case "9": quick_step_back()
            case "10": discard_local_changes()
            case "11": reset_local_to_remote()
            case "12": nuclear_revert()
            case "13": run_time_machine()
            case "q": raise SystemExit(0)
            
        console.print("\n[dim]Press [Enter] to return to dashboard...[/dim]")
        input(" ❯ ")

if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        console.print("\n[bold yellow]⚠ Execution Terminated.[/bold yellow]")
        sys.exit(0)
