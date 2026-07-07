#!/usr/bin/env python3
"""
Dusky Backup Manager - Unified Dotfiles Setup & Linker
Architecture: Arch Linux / Python 3.14 Strict Synchronous I/O
Features: Rich-based Interactive Menu & Command-line flags (--new / --relink)
"""

import os
import sys
import shutil
import argparse
import subprocess
import readline
from pathlib import Path
from dataclasses import dataclass
from typing import Never

# Rich UI components
from rich.console import Console
from rich.panel import Panel
from rich.prompt import Prompt, Confirm
from rich.table import Table
from rich.columns import Columns
from rich.theme import Theme
from rich import box

# Custom Rich Theme
custom_theme = Theme({
    "info": "bold blue",
    "warning": "bold yellow",
    "error": "bold red",
    "success": "bold green",
    "highlight": "bold cyan",
    "muted": "dim white"
})

console = Console(theme=custom_theme)

# Constants
DEFAULT_REPO_NAME = "dusky"
HOME = Path.home()
DOTFILES_DIR = HOME / "dusky"
DOTFILES_LIST = HOME / ".git_dusky_list"
SSH_DIR = HOME / ".ssh"
SSH_KEY_PATH = SSH_DIR / "id_ed25519"
REQUIRED_CMDS = ("git", "ssh", "ssh-keygen", "ssh-agent", "ssh-add")

ssh_agent_pid: str | None = None

@dataclass(frozen=True, kw_only=True, slots=True)
class AppConfig:
    username: str
    email: str
    gh_user: str
    repo: str
    commit_msg: str

    @property
    def repo_url(self) -> str:
        return f"git@github.com:{self.gh_user}/{self.repo}.git"

def run_cmd(args: list[str], capture: bool = True, check: bool = False, input_data: bytes | None = None) -> tuple[int, str, str]:
    """Runs a system command cleanly inheriting parent environment."""
    kwargs = {
        "stdout": subprocess.PIPE if capture else None,
        "stderr": subprocess.PIPE if capture else None,
        "cwd": str(HOME),
        "env": os.environ
    }
    if input_data is not None:
        kwargs["input"] = input_data

    proc = subprocess.run(args, **kwargs)
    stdout = proc.stdout.decode("utf-8", errors="replace") if proc.stdout else ""
    stderr = proc.stderr.decode("utf-8", errors="replace") if proc.stderr else ""

    if check and proc.returncode != 0:
        raise subprocess.CalledProcessError(proc.returncode, args, output=stdout, stderr=stderr)

    return proc.returncode, stdout, stderr

def dotgit(*args: str, input_data: bytes | None = None) -> tuple[int, str, str]:
    """Helper to run git within the bare dotfiles repository context."""
    # We temporarily inject bare repo env vars to the current environment during command execution
    old_work_tree = os.environ.get("GIT_WORK_TREE")
    old_git_dir = os.environ.get("GIT_DIR")
    
    os.environ["GIT_WORK_TREE"] = str(HOME)
    os.environ["GIT_DIR"] = str(DOTFILES_DIR)
    try:
        cmd = [
            "git",
            "--no-optional-locks",
            "--no-advice",
            *args
        ]
        return run_cmd(cmd, input_data=input_data)
    finally:
        # Restore original env to prevent leaks
        if old_work_tree is not None:
            os.environ["GIT_WORK_TREE"] = old_work_tree
        else:
            os.environ.pop("GIT_WORK_TREE", None)
            
        if old_git_dir is not None:
            os.environ["GIT_DIR"] = old_git_dir
        else:
            os.environ.pop("GIT_DIR", None)

def check_dependencies() -> None:
    """Ensures all required binaries are in $PATH."""
    for cmd in REQUIRED_CMDS:
        if not shutil.which(cmd):
            console.print(f"[error]✖ Error: Missing dependency: '{cmd}' is not installed.[/error]")
            sys.exit(1)

def build_dependency_matrix() -> Table:
    """Constructs the visual verification matrix."""
    table = Table(title="Dependency Matrix Verification", box=box.MINIMAL_DOUBLE_HEAD)
    table.add_column("Binary Tool", style="bold cyan")
    table.add_column("Absolute Path", style="muted")
    table.add_column("Status", justify="center")
    
    for cmd in REQUIRED_CMDS:
        path = shutil.which(cmd) or "Not Found"
        table.add_row(cmd, path, "[success]✔[/success]" if path != "Not Found" else "[error]✖[/error]")
            
    return table

def start_ssh_agent() -> None:
    """Launches ssh-agent and sets environment variables in os.environ."""
    global ssh_agent_pid
    try:
        code, stdout, _ = run_cmd(["ssh-agent", "-s"], check=True)
        for line in stdout.splitlines():
            if "SSH_AUTH_SOCK=" in line:
                sock = line.split(";")[0].split("=")[1]
                os.environ["SSH_AUTH_SOCK"] = sock
            if "SSH_AGENT_PID=" in line:
                pid = line.split(";")[0].split("=")[1]
                os.environ["SSH_AGENT_PID"] = pid
                ssh_agent_pid = pid
    except subprocess.CalledProcessError:
        console.print("[error]✖ Error: Failed to start ssh-agent.[/error]")
        sys.exit(1)

def kill_ssh_agent() -> None:
    """Terminates the spawned ssh-agent."""
    global ssh_agent_pid
    if ssh_agent_pid:
        run_cmd(["kill", ssh_agent_pid])
        ssh_agent_pid = None

def generate_ssh_key(email: str) -> None:
    """Generates an ed25519 SSH key pair at the default path (interactive passphrase prompt)."""
    SSH_DIR.mkdir(parents=True, exist_ok=True)
    SSH_DIR.chmod(0o700)

    if SSH_KEY_PATH.is_file():
        console.print(f"[warning]⚠ Warn: SSH key already exists at {SSH_KEY_PATH}[/warning]")
        console.print("[bold cyan]Do you want to overwrite it? [y/N][/bold cyan]")
        ans = input(" ❯ ").strip().lower()
        if ans not in ("y", "yes"):
            console.print("[info]➔ Using existing SSH key.[/info]")
            return
        SSH_KEY_PATH.unlink(missing_ok=True)
        Path(str(SSH_KEY_PATH) + ".pub").unlink(missing_ok=True)

    console.print("[info]Generating new SSH key...[/info]")
    run_cmd(["ssh-keygen", "-t", "ed25519", "-C", email, "-f", str(SSH_KEY_PATH)], capture=False, check=True)
    console.print("[success]✔ SSH key generated successfully.[/success]")

def add_ssh_key_to_agent() -> None:
    """Adds the SSH key to the running agent, prompting for passphrase if needed."""
    console.print("[info]Adding SSH key to agent...[/info]")
    code, _, _ = run_cmd(["ssh-add", str(SSH_KEY_PATH)], capture=False)
    if code != 0:
        console.print("[warning]⚠ Passphrase required. Please enter it now:[/warning]")
        run_cmd(["ssh-add", str(SSH_KEY_PATH)], capture=False, check=True)

def setup_github_ssh_linking(email: str) -> None:
    """Handles generating, displaying, and verifying SSH keys on GitHub."""
    generate_ssh_key(email)
    add_ssh_key_to_agent()

    pub_key_file = Path(str(SSH_KEY_PATH) + ".pub")
    if not pub_key_file.is_file():
        console.print(f"[error]✖ Error: Missing public key file at {pub_key_file}[/error]")
        sys.exit(1)

    pub_key_content = pub_key_file.read_text(encoding="utf-8").strip()

    console.print(Panel(
        f"[warning]ACTION REQUIRED:[/warning] Add this public key to GitHub:\n"
        f"1. Go to: [highlight]https://github.com/settings/keys[/highlight]\n"
        f"2. Click 'New SSH Key', give it a name, and paste the key below:\n\n"
        f"[white]{pub_key_content}[/white]",
        title="GitHub SSH Key Setup",
        border_style="yellow",
        box=box.ROUNDED
    ))
    console.print("Press [highlight][Enter][/highlight] once you have added the key to GitHub")
    input(" ❯ ")

    console.print("[info]Verifying GitHub connection via SSH...[/info]")
    # ssh -T returns 1 on success for GitHub auth checks
    code, _, _ = run_cmd(["ssh", "-T", "-o", "StrictHostKeyChecking=accept-new", "git@github.com"])
    if code == 1:
        console.print("[success]✔ GitHub authentication verified successfully.[/success]")
    else:
        console.print("[error]✖ Error: GitHub SSH connection failed.[/error]")
        sys.exit(1)

def stage_and_commit_dotfiles(commit_msg: str) -> None:
    """Stages dotfiles matching the .git_dusky_list and commits them."""
    if not DOTFILES_LIST.is_file():
        console.print(f"[warning]⚠ Warn: {DOTFILES_LIST} not found. Staging tracked changes only (-u).[/warning]")
        dotgit("add", "-u")
    else:
        console.print("[info]Processing .git_dusky_list...[/info]")
        valid_paths: list[str] = []
        raw_lines = DOTFILES_LIST.read_text(encoding="utf-8").splitlines()
        
        # Optimize deletion check via tracked files set
        tracked_out = dotgit("ls-files", "-z")[1]
        tracked_files = frozenset(filter(None, tracked_out.split('\0')))

        for line in raw_lines:
            clean = line.strip()
            if not clean or clean.startswith("#"):
                continue
            
            # Resolve path relative to HOME securely
            full_path = Path(clean).expanduser()
            if not full_path.is_absolute():
                full_path = HOME / clean

            # Check if file exists on disk OR is tracked by git (for deletions)
            if full_path.exists() or clean in tracked_files:
                valid_paths.append(clean)
            else:
                console.print(f"[muted]  ➔ Skipping missing untracked path: {clean}[/muted]")

        if valid_paths:
            console.print(f"[info]Processing staging payload ({len(valid_paths)} paths)...[/info]")
            payload = "\0".join(valid_paths) + "\0"
            code, _, stderr = dotgit("add", "--pathspec-from-file=-", "--pathspec-file-nul", input_data=payload.encode("utf-8"))
            if code != 0:
                console.print(f"[error]✖ Error staging files:[/error] {stderr.strip()}")
                sys.exit(1)
            
            # Count actual changed files that got staged
            _, diff_out, _ = dotgit("diff", "--cached", "--name-only", "-z")
            staged_count = len(list(filter(None, diff_out.split('\0'))))
            if staged_count > 0:
                console.print(f"[success]✔ Staged {staged_count} changed files.[/success]")
        else:
            console.print("[warning]⚠ Warn: No valid files found in .git_dusky_list. Staging tracked changes only.[/warning]")
            dotgit("add", "-u")

    # Commit
    code_diff, _, _ = dotgit("diff", "--quiet", "--cached")
    if code_diff != 0:
        console.print("[info]Committing changes...[/info]")
        code_com, _, stderr_com = dotgit("commit", "-m", commit_msg)
        if code_com == 0:
            console.print("[success]✔ Changes committed successfully.[/success]")
        else:
            console.print(f"[error]✖ Commit failed:[/error] {stderr_com.strip()}")
    else:
        console.print("[info]➔ Nothing to commit (Working tree clean).[/info]")

def execute_sync(config: AppConfig, mode: str) -> None:
    """Orchestrates the repository sync process."""
    setup_github_ssh_linking(config.email)

    if mode == "NEW":
        console.print(Panel(
            f"[highlight]--- Mode: New Repository Setup ---[/highlight]\n"
            f"Local Path: {DOTFILES_DIR}\n"
            f"Remote URL: {config.repo_url}\n"
            f"Git User:   {config.username} <{config.email}>",
            border_style="cyan",
            box=box.ROUNDED
        ))

        # Initialize local bare repo
        if DOTFILES_DIR.exists():
            console.print(f"[warning]⚠ Warn: Removing existing bare repository at {DOTFILES_DIR}...[/warning]")
            if DOTFILES_DIR.is_dir() and not DOTFILES_DIR.is_symlink():
                shutil.rmtree(DOTFILES_DIR)
            else:
                DOTFILES_DIR.unlink()
        
        DOTFILES_DIR.mkdir(parents=True, exist_ok=True)
        code_init, _, _ = run_cmd(["git", "init", "--bare", str(DOTFILES_DIR)])
        if code_init != 0:
            console.print("[error]✖ Failed to initialize bare repository.[/error]")
            sys.exit(1)

        # Configure local git settings
        dotgit("config", "--local", "status.showUntrackedFiles", "no")
        dotgit("config", "--local", "user.name", config.username)
        dotgit("config", "--local", "user.email", config.email)

        # Link remote
        dotgit("remote", "add", "origin", config.repo_url)

        # Stage and commit files
        stage_and_commit_dotfiles(config.commit_msg)

        # Set branch to main
        dotgit("branch", "-m", "main")

        # Push to origin
        console.print("[info]Pushing first commit to GitHub origin/main...[/info]")
        code_push, _, stderr_push = dotgit("push", "-u", "origin", "main")
        if code_push == 0:
            console.print(Panel("[success]✔ Setup Speedrun Complete! Bare repository is initialized and synced.[/success]", border_style="green", box=box.ROUNDED))
        else:
            console.print(Panel(
                f"[error]✖ Push failed.[/error]\n"
                f"Please ensure you created an [bold yellow]EMPTY[/bold yellow] repository named '{config.repo}' on GitHub.\n"
                f"Error: {stderr_push.strip()}",
                border_style="red",
                box=box.ROUNDED
            ))

    elif mode == "RELINK":
        console.print(Panel(
            f"[highlight]--- Mode: Relink to Existing Repository ---[/highlight]\n"
            f"Local Path: {DOTFILES_DIR}\n"
            f"Remote URL: {config.repo_url}\n"
            f"Git User:   {config.username} <{config.email}>",
            border_style="cyan",
            box=box.ROUNDED
        ))

        # Clone or reuse bare repository
        if DOTFILES_DIR.exists():
            if not DOTFILES_DIR.is_dir():
                console.print(f"[error]✖ Error: {DOTFILES_DIR} exists but is not a directory.[/error]")
                sys.exit(1)
            code_verify, stdout_verify, _ = run_cmd(["git", "--git-dir", str(DOTFILES_DIR), "rev-parse", "--is-bare-repository"])
            if code_verify == 0 and stdout_verify.strip() == "true":
                console.print(f"[info]➔ Using existing bare repository at {DOTFILES_DIR}[/info]")
            else:
                console.print(f"[error]✖ Error: Existing path {DOTFILES_DIR} is not a bare Git repository.[/error]")
                sys.exit(1)
        else:
            console.print("[info]Cloning bare repository from GitHub...[/info]")
            code_clone, _, stderr_clone = run_cmd(["git", "clone", "--bare", config.repo_url, str(DOTFILES_DIR)])
            if code_clone != 0:
                console.print(f"[error]✖ Clone failed. Ensure the repository exists on GitHub.[/error]\nError: {stderr_clone.strip()}")
                sys.exit(1)

        # Configure local git settings
        dotgit("config", "--local", "status.showUntrackedFiles", "no")
        dotgit("config", "--local", "user.name", config.username)
        dotgit("config", "--local", "user.email", config.email)

        # Link/Set Remote url
        dotgit("remote", "set-url", "origin", config.repo_url)

        # Fetch origin
        console.print("[info]Pruning and fetching latest changes from remote...[/info]")
        dotgit("fetch", "--prune", "origin")

        # Mixed Reset to sync index back to HEAD without file overwrites
        code_head, _, _ = dotgit("rev-parse", "--verify", "HEAD")
        if code_head == 0:
            console.print("[info]Performing mixed reset of the index to HEAD...[/info]")
            dotgit("reset", "--mixed", "--quiet", "HEAD")

        # Stage and commit files
        stage_and_commit_dotfiles(config.commit_msg)

        # Push to origin
        code_branch, stdout_branch, _ = dotgit("symbolic-ref", "--quiet", "--short", "HEAD")
        current_branch = stdout_branch.strip() if code_branch == 0 else "main"

        if current_branch:
            console.print(f"[info]Pushing commits to origin/{current_branch}...[/info]")
            code_push, _, stderr_push = dotgit("push", "-u", "origin", current_branch)
            if code_push == 0:
                console.print(Panel("[success]✔ Relink Speedrun Complete! Bare repository is linked and synced.[/success]", border_style="green", box=box.ROUNDED))
            else:
                console.print(Panel(f"[error]✖ Push failed.[/error]\nError: {stderr_push.strip()}", border_style="red", box=box.ROUNDED))

def prompt_configuration() -> AppConfig:
    """Prompts the user for config details interactively using clean fallbacks."""
    console.print("\n[highlight]=== Absolute Engine Parameters ===[/highlight]")
    
    console.print("[bold white]Git User Identity (Name for Git commits, e.g., 'dusk')[/bold white]")
    username = ""
    while not username.strip():
        username = input(" ❯ ").strip()
        if not username:
            console.print("[error]✖ Git User Identity (Name for Git commits) is required and cannot be empty.[/error]")
            
    console.print("[bold white]GitHub Username (Your GitHub account username, e.g., 'yourusername')[/bold white]")
    gh_user = ""
    while not gh_user.strip():
        gh_user = input(" ❯ ").strip()
        if not gh_user:
            console.print("[error]✖ GitHub Username (Your GitHub account username) is required and cannot be empty.[/error]")

    default_email = f"{gh_user.strip()}@users.noreply.github.com"
    console.print(f"[bold white]Git Email Address (Optional, used for commit history) [default: {default_email}][/bold white]")
    email = input(" ❯ ").strip() or default_email

    console.print("[bold white]Target Repository Architecture (The GitHub repository name) [default: dusky][/bold white]")
    repo = input(" ❯ ").strip() or "dusky"

    console.print("[bold white]Initial/Sync Commit Payload (Commit message for syncing changes) [default: Dusky backup sync][/bold white]")
    commit_msg = input(" ❯ ").strip() or "Dusky backup sync"

    return AppConfig(
        username=username.strip(),
        email=email.strip(),
        gh_user=gh_user.strip(),
        repo=repo.strip(),
        commit_msg=commit_msg.strip()
    )

def main() -> Never:
    check_dependencies()

    parser = argparse.ArgumentParser(description="Dusky Dotfiles Engine (Arch Linux / Python 3.14 Strict Mode)")
    group = parser.add_mutually_exclusive_group()
    group.add_argument("-n", "--new", action="store_true", help="Force NEW sequence initialization")
    group.add_argument("-r", "--relink", action="store_true", help="Force RELINK sequence")

    # Config flags
    parser.add_argument("--username", help="Git username")
    parser.add_argument("--email", help="Git email")
    parser.add_argument("--gh-user", help="GitHub username")
    parser.add_argument("--repo", default=DEFAULT_REPO_NAME, help="Repository name")
    parser.add_argument("--commit-msg", help="Commit message")

    args = parser.parse_args()

    mode = "NEW" if args.new else "RELINK" if args.relink else None
    dep_table = build_dependency_matrix()

    # Interactive menu if no mode flag is provided
    if mode is None:
        console.clear()
        cmd_table = Table(title="󰏖 Dusky Engine Commands", show_header=False, box=box.MINIMAL_DOUBLE_HEAD, title_style="bold blue")
        cmd_table.add_column("Key", style="bold cyan")
        cmd_table.add_column("Action", style="bold white")
        cmd_table.add_row("1", "Initialize NEW bare architecture")
        cmd_table.add_row("2", "RELINK existing remote engine")
        cmd_table.add_row("q", "Terminate Execution")

        # Render dependency matrix and commands sequentially (vertical stack)
        console.print(dep_table)
        console.print()
        console.print(cmd_table)
        
        console.print("\n[highlight]Choose Action [1/2/q] [default: 1][/highlight]")
        choice = input(" ❯ ").strip() or "1"
        while choice not in ("1", "2", "q"):
            console.print("[error]✖ Invalid choice. Please choose '1', '2', or 'q'.[/error]")
            choice = input(" ❯ ").strip() or "1"
            
        if choice == "1":
            mode = "NEW"
        elif choice == "2":
            mode = "RELINK"
        else:
            sys.exit(0)

    # Gather parameters (via CLI or Interactive Prompt)
    if args.username and args.gh_user:
        resolved_email = args.email or f"{args.gh_user}@users.noreply.github.com"
        resolved_msg = args.commit_msg or "Dusky backup sync"
        config = AppConfig(
            username=args.username,
            email=resolved_email,
            gh_user=args.gh_user,
            repo=args.repo,
            commit_msg=resolved_msg
        )
    else:
        config = prompt_configuration()

    # Final review confirmation
    console.print(Panel(
        f"Git Identity: {config.username} <{config.email}>\n"
        f"Target Node:  {config.repo_url}\n"
        f"Operation:    {mode}",
        title="Verify Final Deployment Parameters",
        border_style="cyan",
        box=box.ROUNDED
    ))
    console.print("\n[bold cyan]Execute architecture deployment? [Y/n][/bold cyan]")
    ans = input(" ❯ ").strip().lower()
    if ans and ans not in ("y", "yes"):
        console.print("[error]✖ Deployment sequence completely aborted by operator.[/error]")
        sys.exit(1)

    try:
        start_ssh_agent()
        execute_sync(config, mode)
    finally:
        kill_ssh_agent()

    sys.exit(0)

if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        console.print("\n[warning]⚠ Operator Termination Signal Received. Executing shutdown.[/warning]")
        kill_ssh_agent()
        sys.exit(0)
