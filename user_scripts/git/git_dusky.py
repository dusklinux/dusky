#!/usr/bin/env python3
"""
Dusky Dotfiles Manager — bare repository at $HOME/dusky, work tree at $HOME.

Manifest entries scope commits and discards; they never implicitly untrack files.
Selected commits include the current contents of selected paths, including both
sides of renames, while preserving unrelated index entries. Missing manifests
make Commit All update tracked files only; an empty manifest selects nothing.
Interactive prompts use Readline; selectors use the full terminal.
"""

import os
import re
import sys
import json
import shutil
import fnmatch
import subprocess
import tempfile
import fcntl
import readline  # Enable cursor movement, Home/End and deletion in input().
from dataclasses import dataclass
from collections.abc import Callable
from datetime import datetime
from pathlib import Path
from typing import Never

try:
    from rich import box
    from rich.console import Console
    from rich.markup import escape
    from rich.panel import Panel
    from rich.table import Table
except ImportError as _rich_missing:
    raise SystemExit(
        "git_dusky: the 'rich' package is required (pip install rich).\n"
        f"(import failed: {_rich_missing})"
    ) from None

# --- 1. CONSTANTS, STATE & TYPE ALIASES (PEP 695) ---
HOME: Path = Path.home()
GIT_DIR: Path = HOME / "dusky"
WORK_TREE: Path = HOME
DOTFILES_LIST: Path = HOME / ".git_dusky_list"
TIME_MACHINE_BIN: Path = Path(__file__).resolve().parent / "time_machine" / "dusky_time_machine_tui.sh"
MATUGEN_JSON: Path = HOME / ".config" / "matugen" / "generated" / "dusky_tui.json"

DUSKY_VERSION: str = "13.1.0"

type GitResult = tuple[int, str, str]

ANSI_RE: re.Pattern[str] = re.compile(r"\x1b\[[0-9;]*m")


def strip_ansi(text: str) -> str:
    """Removes SGR escape sequences from fzf-bound display strings."""
    return ANSI_RE.sub("", text)


def load_matugen_colors() -> dict[str, str]:
    """Loads dynamic Matugen UI colors from ~/.config/matugen/generated/dusky_tui.json."""
    defaults = {
        "bg": "#1d100a",
        "fg": "#f8ddd2",
        "accent": "#ffb694",
        "error": "#ffb4ab",
        "warning": "#efbc94",
        "success": "#f0be79",
        "muted": "#55433b",
    }
    if MATUGEN_JSON.is_file():
        try:
            data = json.loads(MATUGEN_JSON.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                valid = {k: v for k, v in data.items()
                         if k in defaults and isinstance(v, str)
                         and re.fullmatch(r"#[0-9a-fA-F]{6}", v)}
                return defaults | valid
        except (json.JSONDecodeError, UnicodeError, OSError):
            pass
    return defaults


COLORS: dict[str, str] = load_matugen_colors()

# Set Git environment variables globally so child processes (like fzf and sub-scripts)
# execute within the correct bare repository context.
os.environ["GIT_DIR"] = str(GIT_DIR)
os.environ["GIT_WORK_TREE"] = str(WORK_TREE)

console = Console()


# --- 2. SYNCHRONOUS I/O HELPERS ---
def ask(prompt: str = " ❯ ") -> str:
    """Reads a stripped line, exiting gracefully on Ctrl-D (EOF) instead of crashing."""
    try:
        return input(prompt).strip()
    except EOFError:
        console.print()
        raise SystemExit(0) from None


def ask_yesno(prompt: str, *, default: bool = False) -> bool:
    """Asks a normalized yes/no question; empty answer takes the default."""
    suffix = " (Y/n)" if default else " (y/N)"
    while True:
        ans = ask(f"{prompt}{suffix}").lower()
        if not ans:
            return default
        if ans in ("y", "yes"):
            return True
        if ans in ("n", "no"):
            return False
        console.print("[bold red]✖ Please answer y or n.[/bold red]")


# --- 3. SYNCHRONOUS GIT ENGINE ---
def run_git(
    *args: str,
    capture: bool = True,
    check: bool = False,
    input_data: bytes | None = None,
    literal_pathspecs: bool = False,
    index_file: Path | None = None,
) -> GitResult:
    """Executes Git with strict standard I/O synchronization and environment isolation."""
    git_env = os.environ.copy()

    # Explicitly overriding ENV bounds guarantees Wayland context won't leak variables.
    git_env["GIT_WORK_TREE"] = str(WORK_TREE)
    git_env["GIT_DIR"] = str(GIT_DIR)

    # Inherited alternate indexes/pathspec modes must not redirect these actions.
    for key in ("GIT_INDEX_FILE", "GIT_COMMON_DIR", "GIT_OBJECT_DIRECTORY",
                "GIT_ALTERNATE_OBJECT_DIRECTORIES", "GIT_PREFIX",
                "GIT_LITERAL_PATHSPECS", "GIT_GLOB_PATHSPECS",
                "GIT_NOGLOB_PATHSPECS", "GIT_ICASE_PATHSPECS"):
        git_env.pop(key, None)
    if literal_pathspecs:
        git_env["GIT_LITERAL_PATHSPECS"] = "1"

    if index_file is not None:
        git_env["GIT_INDEX_FILE"] = str(index_file)

    cmd = [
        "git",
        "--no-optional-locks",
        "--no-advice",
        "-c", "core.quotepath=false",
        *args
    ]

    kwargs: dict[str, object] = {
        "stdout": subprocess.PIPE if capture else None,
        "stderr": subprocess.PIPE if capture else None,
        "env": git_env,
        "cwd": str(WORK_TREE),
    }
    if input_data is not None:
        kwargs["input"] = input_data

    proc = subprocess.run(cmd, **kwargs)

    if check and proc.returncode != 0:
        if capture and proc.stderr:
            console.print(
                "[bold red]Git Internal Error:[/bold red]\n"
                + escape(proc.stderr.decode("utf-8", errors="replace").strip())
            )
        raise subprocess.CalledProcessError(
            proc.returncode, cmd, output=proc.stdout, stderr=proc.stderr
        )

    return (
        proc.returncode,
        proc.stdout.decode("utf-8", errors="surrogateescape") if proc.stdout else "",
        proc.stderr.decode("utf-8", errors="replace") if proc.stderr else "",
    )


def check_dependencies() -> None:
    """Validates the execution environment and required binaries."""
    if sys.version_info < (3, 12):
        console.print(
            f"[bold red]✖ Error:[/bold red] Python 3.12+ required "
            f"(found {sys.version.split()[0]})."
        )
        sys.exit(1)

    for cmd in ("git", "fzf", "delta"):
        if not shutil.which(cmd):
            console.print(f"[bold red]✖ Error:[/bold red] '{cmd}' binary missing from $PATH.")
            sys.exit(1)

    if not GIT_DIR.is_dir():
        console.print(f"[bold red]✖ Error:[/bold red] Bare repository target missing: {GIT_DIR}")
        sys.exit(1)


# --- 4. PATHSPEC ORCHESTRATOR ---
def git_dir_rel() -> str | None:
    """Relative path of GIT_DIR inside the work tree ('dusky'), or None if outside."""
    try:
        rel = os.path.relpath(GIT_DIR, WORK_TREE)
    except ValueError:
        return None
    return None if rel == ".." or rel.startswith("../") else rel


def is_internal_gitdir(path: str) -> bool:
    """True when a work-tree-relative path lives inside GIT_DIR itself."""
    rel = git_dir_rel()
    if not rel or rel == ".":
        return False
    return path == rel or path.startswith(rel + "/")


def matches_pathspec(path: str | None, valid_paths: list[str]) -> bool:
    """Evaluates Git-style globs and exact boundaries purely in Python.

    A manifest entry of "." means the entire work tree. Paths inside GIT_DIR
    are always excluded, and compiler caches are ignored by policy.
    """
    if not path or is_internal_gitdir(path):
        return False

    # Explicitly ignore compiler cache folders/files
    if "__pycache__" in Path(path).parts or path.endswith((".pyc", ".pyo", ".pyd")):
        return False

    for vp in valid_paths:
        vp_clean = vp.rstrip("/")
        if vp_clean == ".":
            return True
        # Exact match or Directory prefix match
        if path == vp_clean or path.startswith(vp_clean + "/"):
            return True
        # Glob match (e.g., *.conf, **/*.sh)
        if fnmatch.fnmatch(path, vp_clean) or fnmatch.fnmatch(path, vp_clean + "/*"):
            return True

    return False


def get_list_pathspecs() -> list[str] | None:
    """Extracts valid paths ensuring boundary limitations to $HOME.

    Returns None when the manifest file is missing (blanket mode),
    otherwise the deduplicated list (possibly empty).
    """
    if not DOTFILES_LIST.is_file():
        return None

    raw_lines = DOTFILES_LIST.read_text(encoding="utf-8").splitlines()
    valid_paths: list[str] = []

    for line in raw_lines:
        clean = line.strip()
        if not clean or clean.startswith("#"):
            continue
        try:
            target = Path(os.path.expandvars(clean)).expanduser()
            if not target.is_absolute():
                target = WORK_TREE / target

            normalized_abs = Path(os.path.normpath(target))
            if normalized_abs == WORK_TREE:
                valid_paths.append(".")
                continue
            if normalized_abs.is_relative_to(WORK_TREE):
                rel_path = normalized_abs.relative_to(WORK_TREE)
                if is_internal_gitdir(str(rel_path)):
                    console.print(
                        f"[bold red]✖ Security Block:[/bold red] "
                        f"path inside GIT_DIR ignored -> {escape(clean)}"
                    )
                    continue
                if str(rel_path) not in valid_paths:
                    valid_paths.append(str(rel_path))
            else:
                console.print(
                    f"[bold red]✖ Security Block:[/bold red] "
                    f"Path escaped work-tree -> {escape(clean)}"
                )
        except (ValueError, OSError):
            continue

    return valid_paths


def changed_entries(
    *, tracked_only: bool = False, scope: list[str] | None = None,
) -> list[tuple[str, str | None, str]]:
    """Read NUL-delimited status, limiting filesystem scans to manifest roots."""
    roots = set()
    for pattern in scope or []:
        # Scan a literal ancestor of a glob, then use one matching policy below.
        wildcard = re.search(r"[?*\[]", pattern)
        if wildcard:
            prefix = pattern[:wildcard.start()]
            root = prefix.rsplit("/", 1)[0] if "/" in prefix else "."
        else:
            root = pattern
        roots.add(root or ".")
    if "." in roots:
        roots.clear()
    paths = [f":(top,literal){root}" for root in sorted(roots)]
    batches = [paths[i:i + 128] for i in range(0, len(paths), 128)] or [[]]
    result = {}
    for batch in batches:
        _, out, _ = run_git(
            "status", "--porcelain=v1", "-z",
            "--untracked-files=no" if tracked_only else "--untracked-files=all",
            "--", *batch, check=True,
        )
        entries = iter(out.split("\0"))
        for entry in entries:
            if not entry:
                continue
            status, path = entry[:2], entry[3:]
            old = next(entries) if "R" in status or "C" in status else None
            if is_internal_gitdir(path) or (old and is_internal_gitdir(old)):
                continue
            result[(path, old, status)] = None
    return list(result)


def stage_entries(entries: list[tuple[str, str | None, str]], *, local_only: bool = False) -> None:
    """Stage selected work-tree changes; leave unrelated staged files alone."""
    if any(status in {"DD", "AU", "UD", "UA", "DU", "AA", "UU"} for _, _, status in entries):
        console.print("[bold red]✖ Resolve merge conflicts before selecting files to commit.[/bold red]")
        return
    selected: set[str] = set()
    unstaged: set[str] = set()
    for path, old, status in entries:
        selected.add(path)
        if old and "R" in status:
            selected.add(old)
        if status[1] != " ":
            unstaged.add(path)
            # Staged renames already removed the source from the index.
            if old and status[1] == "R":
                unstaged.add(old)
    if unstaged:
        payload = os.fsencode("\0".join(sorted(unstaged)) + "\0")
        run_git("add", "--all", "--pathspec-from-file=-", "--pathspec-file-nul",
                input_data=payload, literal_pathspecs=True, check=True)
    if selected:
        commit_and_push(sorted(selected), local_only=local_only)
    else:
        console.print("[green]No matching changes to commit.[/green]")


def scoped_entries(*, tracked_only: bool = False) -> list[tuple[str, str | None, str]]:
    paths = get_list_pathspecs()
    if paths == []:
        console.print("[yellow]The manifest has no valid paths; nothing selected.[/yellow]")
        return []
    return [entry for entry in changed_entries(tracked_only=tracked_only, scope=paths)
            if paths is None or matches_pathspec(entry[0], paths)
            or matches_pathspec(entry[1], paths)]


def sync_all(local_only: bool = False) -> None:
    """Commit matching changes without implicitly untracking other files."""
    stage_entries(scoped_entries(tracked_only=not DOTFILES_LIST.is_file()), local_only=local_only)


def sync_single() -> None:
    """Select literal filenames, with escaped control characters for display."""
    entries = scoped_entries()
    choices = {}
    displays = []
    for number, entry in enumerate(entries, 1):
        path, old, status = entry
        display = f"{number}: {format_status_badge(status)} {repr(path)[1:-1]}"
        if old:
            display += f" (from {repr(old)[1:-1]})"
        choices[strip_ansi(display)] = entry
        displays.append(display)
    selected = fzf_select(displays, prompt="Stage Files", multi=True)
    if selected:
        stage_entries([choices[strip_ansi(line)] for line in selected])


def format_status_badge(code: str) -> str:
    """Formats 2-character git status code into a colorized 4-character badge for fzf display."""
    match code:
        case " M":
            return "\033[33m[ M]\033[0m"   # Yellow: Unstaged Modified
        case "M ":
            return "\033[32m[M ]\033[0m"   # Green: Staged Modified
        case "MM":
            return "\033[35m[MM]\033[0m"   # Magenta: Staged + Modified
        case " D" | "D ":
            return "\033[31m[ D]\033[0m" if code == " D" else "\033[31m[D ]\033[0m"  # Red: Deletion
        case "A " | "AM":
            return "\033[36m[A ]\033[0m" if code == "A " else "\033[36m[AM]\033[0m"  # Cyan: Addition
        case "??" | "  ":
            return "\033[90m[??]\033[0m"   # Muted Gray: Untracked
        case _:
            return f"\033[36m[{code}]\033[0m"


def fzf_select(
    choices: list[str],
    prompt: str = "Select",
    multi: bool = False,
    preview: str | None = None,
    header: str | None = None
) -> list[str]:
    """Feeds NUL-terminated strings to FZF safely via synchronous PIPEs."""
    if not choices:
        return []

    fzf_colors = (
        f"bg+:{COLORS['muted']},bg:{COLORS['bg']},"
        f"fg:{COLORS['fg']},fg+:{COLORS['fg']},"
        f"header:{COLORS['accent']},info:{COLORS['accent']},"
        f"pointer:{COLORS['success']},marker:{COLORS['success']},"
        f"prompt:{COLORS['accent']},border:{COLORS['muted']},"
        f"label:{COLORS['accent']}"
    )

    fzf_cmd = [
        "fzf",
        "--read0",
        "--print0",
        "--ansi",
        f"--color={fzf_colors}",
        f"--prompt={prompt} ❯ ",
        "--pointer=❯ ",
        "--marker=✔ ",
        "--info=inline",
        "--no-height",
        "--layout=reverse",
        "--border=rounded",
    ]
    if header:
        fzf_cmd.append(f"--header={header}")
    elif multi:
        default_header = (
            " \033[90m[TAB]\033[0m Mark  \033[90m[Ctrl-A]\033[0m All  \033[90m[Ctrl-D]\033[0m Clear  \033[90m[ENTER]\033[0m Confirm  │  "
            "\033[33m[ M]\033[0m Mod  \033[32m[M ]\033[0m Staged  \033[35m[MM]\033[0m Both  \033[31m[ D]\033[0m Del  \033[36m[A ]\033[0m Add  \033[90m[??]\033[0m New"
        )
        fzf_cmd.append(f"--header={default_header}")

    if multi:
        fzf_cmd.append("--multi")
        fzf_cmd.append("--bind=ctrl-a:select-all,ctrl-d:deselect-all")
    if preview:
        fzf_cmd.extend(["--preview", preview])

    payload = "\0".join(choices) + "\0"

    fzf_env = os.environ.copy()
    for key in ("FZF_DEFAULT_OPTS", "FZF_DEFAULT_OPTS_FILE", "FZF_DEFAULT_COMMAND"):
        fzf_env.pop(key, None)
    # Previews use POSIX commands even when the login shell is fish.
    fzf_env["SHELL"] = "/bin/sh"
    fzf_env["GIT_DIR"] = str(GIT_DIR)
    fzf_env["GIT_WORK_TREE"] = str(WORK_TREE)
    proc = subprocess.run(
        fzf_cmd,
        input=payload.encode("utf-8", errors="surrogateescape"),
        stdout=subprocess.PIPE,
        stderr=None,
        env=fzf_env,
        cwd=str(WORK_TREE),
    )

    if proc.returncode not in (0, 1, 130):
        raise RuntimeError(f"fzf failed with exit status {proc.returncode}")
    if proc.returncode != 0:
        return []

    return [line for line in proc.stdout.decode("utf-8", errors="surrogateescape").split("\0") if line]


def commit_selected_index(files: list[str], message: str) -> None:
    """Commit selected staged entries, including deletions whose files still exist."""
    for state in ("MERGE_HEAD", "CHERRY_PICK_HEAD", "REVERT_HEAD", "rebase-merge", "rebase-apply"):
        if (GIT_DIR / state).exists():
            raise RuntimeError("Finish the active merge/rebase/cherry-pick before committing selected files.")
    _, stages, _ = run_git("ls-files", "--stage", "-z", check=True)
    selected = set(files)
    records = {}
    for entry in stages.split("\0"):
        if not entry:
            continue
        meta, path = entry.split("\t", 1)
        if meta.split()[2] != "0":
            raise RuntimeError("Resolve all index conflicts before committing selected files.")
        if path in selected:
            records[path] = entry
    _, object_format, _ = run_git("rev-parse", "--show-object-format", check=True)
    zero = "0" * (64 if object_format.strip() == "sha256" else 40)
    updates = [f"0 {zero}\t{path}\0" for path in files]
    updates += [entry + "\0" for entry in records.values()]
    with tempfile.TemporaryDirectory(prefix="dusky-index-", dir=GIT_DIR) as temp:
        index = Path(temp) / "index"
        head_exists = run_git("rev-parse", "--verify", "HEAD")[0] == 0
        run_git("read-tree", "HEAD" if head_exists else "--empty", index_file=index, check=True)
        run_git("update-index", "-z", "--index-info", input_data=os.fsencode("".join(updates)),
                index_file=index, check=True)
        run_git("commit", "-m", message, index_file=index, check=True)
    # Hooks may have rewritten selected entries. Update only those real index
    # entries; all unrelated staged changes and all disk files remain intact.
    try:
        run_git("reset", "--quiet", "HEAD", "--pathspec-from-file=-", "--pathspec-file-nul",
                input_data=os.fsencode("\0".join(files) + "\0"), literal_pathspecs=True, check=True)
    except subprocess.CalledProcessError as exc:
        raise RuntimeError("Commit succeeded, but updating selected index entries failed; inspect git status before retrying.") from exc


def commit_and_push(files: list[str] | None = None, local_only: bool = False) -> None:
    """Commit the selected staged paths and optionally push the resulting commit."""
    _, staged_out, _ = run_git("diff", "--cached", "--name-only", "--no-renames", "-z", check=True)
    staged = {path for path in staged_out.split("\0") if path}
    commit_files = sorted(staged if files is None else staged.intersection(files))
    if not commit_files:
        console.print("[yellow]Nothing staged for the selected files.[/yellow]")
        return

    console.print("\n[bold cyan]Commit Message (or type 'abort' to cancel)[/bold cyan]")
    while True:
        msg = ask()
        if not msg:
            console.print("[bold red]✖ Error: Commit message cannot be empty.[/bold red]")
            continue
        if msg.lower() in ("abort", "q"):
            console.print("[bold yellow]⚠ Aborted: Commit cancelled by user.[/bold yellow]")
            return
        break

    try:
        commit_selected_index(commit_files, msg)
    except subprocess.CalledProcessError:
        console.print("[bold red]✖ Commit failed (Hooks/Formatting block).[/bold red]")
        return

    if local_only:
        console.print("[bold green]✔[/bold green] Committed changes locally.")
        return

    if ask_yesno("Push to the configured upstream?", default=True):
        safe_push()


def upstream_target(branch: str) -> tuple[str, str] | None:
    """Return the actual configured remote and full destination branch ref."""
    _, out, _ = run_git("for-each-ref", "--format=%(upstream:remotename)%00%(upstream:remoteref)",
                        f"refs/heads/{branch}", check=True)
    remote, _, ref = out.strip().partition("\0")
    return (remote, ref) if remote and ref else None


def safe_push(branch: str | None = None) -> bool:
    """Push to the configured upstream and check every recovery operation."""
    _, current, _ = run_git("branch", "--show-current", check=True)
    current = current.strip()
    if not current or (branch is not None and branch != current):
        console.print("[red]Push requires the active named branch.[/red]")
        return False
    branch = current
    target = upstream_target(branch)
    if target is None:
        if not ask_yesno(f"Set upstream to origin/{branch} and push?", default=True):
            return False
        remote, ref = "origin", f"refs/heads/{branch}"
    else:
        remote, ref = target
    push_args = ["push", "--set-upstream", "--", remote, f"refs/heads/{branch}:{ref}"]
    if run_git(*push_args, capture=False)[0] == 0:
        console.print("[green]✔ Push successful.[/green]")
        return True
    if run_git("fetch", "--", remote, ref, capture=False)[0] != 0:
        return False
    _, fetched, _ = run_git("rev-parse", "--verify", "FETCH_HEAD^{commit}", check=True)
    fetched = fetched.strip()
    _, counts, _ = run_git("rev-list", "--left-right", "--count", f"HEAD...{fetched}", check=True)
    ahead, behind = map(int, counts.split())
    if not behind:
        console.print("[red]Push rejected; check remote permissions, hooks or branch protection.[/red]")
        return False
    if changed_entries(tracked_only=True):
        console.print("[yellow]Commit or stash tracked edits before reconciling with upstream.[/yellow]")
        return False
    if not ahead:
        if not ask_yesno(f"Fast-forward to {remote}/{ref.removeprefix('refs/heads/')}?", default=False):
            return False
        run_git("merge", "--ff-only", fetched, capture=False, check=True)
        return True
    console.print("1) Rebase local commits onto upstream\n2) Force push with lease\n3) Cancel")
    choice = ask("Choose [1/2/3] (default 3): ") or "3"
    if choice == "1":
        # Do not auto-abort: an existing or newly conflicted rebase needs inspection.
        if run_git("rebase", "--no-autostash", fetched, capture=False)[0]:
            console.print("[yellow]Rebase stopped. Resolve it or run git rebase --abort before retrying.[/yellow]")
            return False
        run_git(*push_args, capture=False, check=True)
    elif choice == "2" and ask_yesno(f"Overwrite {remote}/{ref} with local history?"):
        run_git("push", f"--force-with-lease={ref}:{fetched}", "--", remote,
                f"refs/heads/{branch}:{ref}", capture=False, check=True)
    else:
        return False
    console.print("[green]✔ Push successful.[/green]")
    return True


def discard_local_changes() -> None:
    """Discards uncommitted changes (staged + unstaged), honoring manifest scope."""
    valid_paths = get_list_pathspecs()
    if valid_paths == []:
        console.print("[yellow]Empty manifest: nothing to discard.[/yellow]")
        return
    changed_tracked: list[str] = []
    untracked_to_delete: list[str] = []
    tracked_scope: list[str] = []
    for path, orig_path, status_code in scoped_entries():
        if status_code == "??":
            untracked_to_delete.append(path)
        else:
            changed_tracked.append(f"➔ {path}" + (f" (from {orig_path})" if orig_path else ""))
            tracked_scope.append(path)
            if orig_path:
                tracked_scope.append(orig_path)

    if not changed_tracked and not untracked_to_delete:
        console.print("[bold green]✔[/bold green] Working tree already clean. No changes to discard.")
        return

    scope_note = (
        "tracked files listed in your manifest"
        if valid_paths is not None
        else "ALL tracked files in the work tree"
    )
    console.print(Panel.fit(
        "[bold red]!!! DISCARD LOCAL CHANGES !!![/bold red]\n"
        f"This will permanently erase local changes of your choice.\n"
        f"Revert scope: [bold]{scope_note}[/bold].",
        border_style="red"
    ))

    if changed_tracked:
        console.print("\n[bold yellow]The following modified/deleted files can be REVERTED:[/bold yellow]")
        for item in sorted(changed_tracked):
            console.print(f"  [red]{escape(item)}[/red]")

    if untracked_to_delete:
        console.print("\n[bold yellow]The following untracked files can be PERMANENTLY DELETED:[/bold yellow]")
        for item in sorted(untracked_to_delete):
            console.print(f"  [red]➔ {escape(item)}[/red]")
    console.print()

    revert_tracked = False
    delete_untracked = False

    if changed_tracked:
        revert_tracked = ask_yesno(f"Revert all modifications in {scope_note}?")

    if untracked_to_delete:
        delete_untracked = ask_yesno("Permanently delete all listed untracked files?")

    if not revert_tracked and not delete_untracked:
        console.print("[bold yellow]⚠ Aborted: No changes were discarded.[/bold yellow]")
        return

    # A staged deletion and an untracked replacement can share the same path.
    # Respect the separate approval for deleting that replacement.
    collisions = {tracked for tracked in tracked_scope for untracked in untracked_to_delete
                  if tracked == untracked.rstrip("/")
                  or tracked.startswith(untracked.rstrip("/") + "/")
                  or untracked.startswith(tracked + "/")}
    if revert_tracked and collisions and not delete_untracked:
        console.print("[yellow]Restore blocked: untracked replacements would be overwritten. No files changed.[/yellow]")
        return
    if revert_tracked and run_git("rev-parse", "--verify", "HEAD")[0] != 0:
        console.print("[red]No HEAD exists to restore. No files changed.[/red]")
        return

    try:
        # Remove approved untracked files first, so we cannot delete a file that
        # restore has just recreated from HEAD.
        if delete_untracked:
            for path in untracked_to_delete:
                full_path = WORK_TREE / path
                if full_path.is_symlink() or full_path.is_file():
                    full_path.unlink()
                elif full_path.is_dir():
                    shutil.rmtree(full_path)
            console.print("[green]✔ Approved untracked files deleted.[/green]")
        if revert_tracked:
            payload = os.fsencode("\0".join(dict.fromkeys(tracked_scope)) + "\0")
            run_git("restore", "--source=HEAD", "--staged", "--worktree", "--quiet",
                    "--pathspec-from-file=-", "--pathspec-file-nul",
                    input_data=payload, check=True, literal_pathspecs=True)
            console.print("[green]✔ Tracked files restored.[/green]")
    except subprocess.CalledProcessError:
        console.print("[red]✖ Discard failed; inspect Git status before retrying.[/red]")


def reset_local_to_remote() -> None:
    """Hard resets the local repository to match the remote branch tracking state."""
    console.print(Panel.fit(
        "[bold red]⚠ RESET LOCAL STATE TO MATCH GITHUB ⚠[/bold red]\n"
        "This will discard all local commits that haven't been pushed to GitHub\n"
        "AND erase all uncommitted edits on your disk, resetting everything to match the remote.",
        border_style="red"
    ))

    _, branch_out, _ = run_git("branch", "--show-current")
    branch_out = branch_out.strip()
    if not branch_out:
        console.print("[bold red]✖ Error: Detached HEAD state detected.[/bold red]")
        return

    if ask_yesno(f"Reset local state and overwrite all files to match origin/{branch_out}?", default=False):
        try:
            console.print("[bold blue]Fetching latest state from GitHub...[/bold blue]")
            run_git("fetch", "origin", f"refs/heads/{branch_out}", capture=False, check=True)

            console.print(f"[bold blue]Hard resetting to origin/{branch_out}...[/bold blue]")
            run_git("reset", "--hard", "FETCH_HEAD", "--quiet", capture=False, check=True)
            console.print("[bold green]✔[/bold green] Local state successfully synced with GitHub remote.")
        except subprocess.CalledProcessError:
            console.print("[bold red]✖ Sync operation failed.[/bold red]")


def quick_step_back() -> None:
    """Rolls back the repository by exactly 1 commit on both local and remote."""
    console.print(Panel.fit(
        "[bold red]⚠ DELETE LAST COMMIT FROM REMOTE ⚠[/bold red]\n"
        "This will hard-reset the local repository to HEAD~1 and force-push to origin,\n"
        "permanently deleting the last commit from both local and remote history.",
        border_style="red"
    ))

    code, log_out, _ = run_git("log", "--format=%h", "-n", "2")
    if code != 0 or not log_out or len(log_out.splitlines()) < 2:
        console.print("[bold red]✖ Error:[/bold red] Cannot step back. Must have at least two commits in history.")
        return

    if ask_yesno("Step back 1 commit on both local and remote?", default=False):
        try:
            run_git("reset", "--hard", "HEAD~1", "--quiet", capture=False, check=True)
            console.print("[bold green]✔[/bold green] Local repository reset to HEAD~1.")

            _, branch_out, _ = run_git("branch", "--show-current")
            branch_out = branch_out.strip()

            if not branch_out:
                console.print("[bold red]✖ Error: Detached HEAD state detected.[/bold red] Aborting remote push.")
                return

            console.print(f"[bold blue]Force-pushing to origin/{branch_out}...[/bold blue]")
            run_git("push", "--force-with-lease", "origin", f"refs/heads/{branch_out}:refs/heads/{branch_out}", capture=False, check=True)
            console.print("[bold green]✔[/bold green] Step back 1 commit complete on remote.")
        except subprocess.CalledProcessError:
            console.print("[bold red]✖ Step back operation failed.[/bold red]")


def undo_local_commits_to_commit() -> None:
    """Safe mixed reset to a selected past commit (uncommits files, keeping disk modifications)."""
    console.print(Panel.fit(
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
    console.print(f"\n[bold yellow]Target Commit:[/bold yellow] {escape(target[0])}")

    if ask_yesno(f"Reset local HEAD to {commit_hash} and preserve edits?", default=False):
        try:
            run_git("reset", commit_hash, capture=False, check=True)
            console.print(f"[bold green]✔[/bold green] Local HEAD reset to {commit_hash}. Changes preserved in working tree.")
        except subprocess.CalledProcessError:
            console.print("[bold red]✖ Reset operation failed.[/bold red]")


def delete_local_commits_to_commit() -> None:
    """Destructive hard reset to a selected past commit (uncommits files and wipes edits)."""
    console.print(Panel.fit(
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
    console.print(f"\n[bold yellow]Target Commit:[/bold yellow] {escape(target[0])}")

    if ask_yesno(f"Delete all local commits since {commit_hash} and discard all their edits?", default=False):
        try:
            run_git("reset", "--hard", commit_hash, "--quiet", capture=False, check=True)
            console.print(f"[bold green]✔[/bold green] Local state reset to {commit_hash}. Changes discarded.")
        except subprocess.CalledProcessError:
            console.print("[bold red]✖ Reset operation failed.[/bold red]")


def safe_revert_last_commit() -> None:
    """Safe non-destructive revert that appends a new commit undoing the last commit."""
    console.print(Panel.fit(
        "[bold green]✔ UNDO LAST COMMIT SAFELY (Create Revert Commit) ✔[/bold green]\n"
        "This will create a new commit that undoes the changes of the last commit,\n"
        "preserving the commit history without rewriting it.",
        border_style="green"
    ))

    code, log_out, _ = run_git("log", "-n", "1")
    if code != 0 or not log_out:
        console.print("[bold red]✖ Error:[/bold red] No history found to revert.")
        return

    if ask_yesno("Execute safe revert of the last commit?", default=True):
        try:
            run_git("revert", "--no-edit", "HEAD", capture=False, check=True)
            console.print("[bold green]✔[/bold green] Safe revert commit created locally.")

            if ask_yesno("Push the revert commit to remote?", default=True):
                safe_push()
        except subprocess.CalledProcessError:
            console.print("[bold red]✖ Safe revert operation aborted or failed.[/bold red]")


def show_delta() -> None:
    """Pipes differential directly through Delta. Pure view — never mutates the index."""
    console.print("[bold blue]Executing Delta differential...[/bold blue]")
    code, _, _ = run_git("-c", "core.pager=delta", "diff", "HEAD", capture=False)
    if code != 0:
        console.print("[bold yellow]⚠[/bold yellow] No HEAD to diff against yet (empty repository?).")


def nuclear_revert() -> None:
    """Absolute destructive timeline sync. Hard resets local tree and force-pushes."""
    console.print(Panel.fit(
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
    console.print(f"\n[bold yellow]Target Commit:[/bold yellow] {escape(target[0])}")

    if ask_yesno(f"Execute HARD RESET to {commit_hash}? (Wipes local tracked changes)", default=False):
        try:
            run_git("reset", "--hard", commit_hash, "--quiet", capture=False, check=True)
            console.print(f"[bold green]✔[/bold green] Tracked files restored to {commit_hash}.")

            if ask_yesno("FORCE PUSH to overwrite remote timeline?", default=False):
                _, branch_out, _ = run_git("branch", "--show-current")
                branch_out = branch_out.strip()

                if not branch_out:
                    console.print("[bold red]✖ Error: Detached HEAD state detected.[/bold red] Aborting force push.")
                    return

                run_git("push", "--force-with-lease", "origin", f"refs/heads/{branch_out}:refs/heads/{branch_out}", capture=False, check=True)
                console.print("[bold green]✔[/bold green] Remote repository obliteration complete.")
        except subprocess.CalledProcessError:
            console.print("[bold red]✖ Force reset operation interrupted by error.[/bold red]")


def run_time_machine() -> None:
    """Handoff execution to the highly-optimized Ephemeral Bash TUI."""
    if TIME_MACHINE_BIN.is_file() and os.access(TIME_MACHINE_BIN, os.X_OK):
        console.print("[bold blue]Engaging ZRAM Ephemeral Time Machine...[/bold blue]")
        subprocess.run([str(TIME_MACHINE_BIN)], check=True)
    else:
        console.print(f"[bold red]✖ Error:[/bold red] Time machine binary not found or not executable at {escape(str(TIME_MACHINE_BIN))}")


def checkout_pr() -> None:
    """Fetches a GitHub Pull Request by URL or number and checks out a local branch without merging."""
    console.print(Panel.fit(
        "[bold cyan]󰏖 CHECKOUT GITHUB PULL REQUEST[/bold cyan]\n"
        "Fetch a Pull Request locally for editing/testing without merging into main.",
        border_style="cyan"
    ))
    console.print("[bold cyan]Enter GitHub PR URL or PR Number (e.g. 268 or https://github.com/owner/repo/pull/268)[/bold cyan]")
    user_input = ask()
    if not user_input or user_input.lower() in ("q", "abort", "exit"):
        console.print("[bold yellow]⚠ Aborted PR checkout.[/bold yellow]")
        return

    match = re.fullmatch(r"(?:https://github\.com/[^/]+/[^/]+/pull/)?([1-9]\d*)/?", user_input)
    if not match:
        console.print(f"[bold red]✖ Error: Could not parse a valid PR number from '{escape(user_input)}'.[/bold red]")
        return

    pr_num = match.group(1)
    branch_name = f"pr/{pr_num}"

    code, _, _ = run_git("show-ref", "--verify", "--quiet", f"refs/heads/{branch_name}")
    if code == 0:
        console.print(f"[bold yellow]⚠[/bold yellow] Local branch '{branch_name}' already exists — switching to it without fetching.")
        try:
            run_git("switch", branch_name, capture=False, check=True)
            console.print(f"[bold green]✔[/bold green] Switched to existing branch '{branch_name}'.")
        except subprocess.CalledProcessError:
            console.print(f"[bold red]✖ Failed to switch to '{branch_name}'.[/bold red]")
        return

    console.print(f"[bold blue]Fetching PR #{pr_num} into branch '{branch_name}'...[/bold blue]")
    try:
        run_git("fetch", "origin", f"pull/{pr_num}/head:{branch_name}", capture=False, check=True)
        console.print(f"[bold blue]Switching to branch '{branch_name}'...[/bold blue]")
        run_git("switch", branch_name, capture=False, check=True)
        console.print(Panel.fit(
            f"[bold green]✔ Checked out PR #{pr_num} on branch '{branch_name}'![/bold green]\n\n"
            f"You can now edit and test your files locally.\n"
            f"When done, use option 7 ([bold yellow]Branch Management[/bold yellow]) to return to 'main'.",
            border_style="green"
        ))
    except subprocess.CalledProcessError:
        console.print(f"[bold red]✖ Failed to checkout PR #{pr_num}. Make sure the PR exists on GitHub remote.[/bold red]")


def create_branch() -> None:
    """Creates a new local branch and optionally checks it out."""
    console.print("\n[bold cyan]Enter new branch name (or 'abort' to cancel):[/bold cyan]")
    name = ask()
    if not name or name.lower() in ("abort", "q"):
        console.print("[bold yellow]⚠ Branch creation cancelled.[/bold yellow]")
        return

    clean_name = name.replace(" ", "-")

    if clean_name.startswith("-") or run_git("check-ref-format", "--branch", clean_name)[0]:
        console.print("[red]Invalid branch name.[/red]")
        return
    checkout = ask_yesno(f"Switch to branch '{clean_name}' immediately?", default=True)

    try:
        if checkout:
            run_git("switch", "-c", clean_name, capture=False, check=True)
            console.print(f"[bold green]✔ Branch '{clean_name}' created and switched to successfully.[/bold green]")
        else:
            run_git("branch", clean_name, capture=False, check=True)
            console.print(f"[bold green]✔ Branch '{clean_name}' created successfully.[/bold green]")
    except subprocess.CalledProcessError:
        console.print(f"[bold red]✖ Failed to create branch '{clean_name}'.[/bold red]")


def switch_branch() -> None:
    """Interactively lists and switches local branches using FZF with commit preview."""
    code, branches_out, _ = run_git("branch", "--format=%(refname:short)")
    if code != 0 or not branches_out.strip():
        console.print("[bold red]✖ Error: Failed to list local branches.[/bold red]")
        return

    branches = [b.strip() for b in branches_out.splitlines() if b.strip()]
    if not branches:
        console.print("[bold yellow]⚠ No local branches found.[/bold yellow]")
        return

    _, current_branch, _ = run_git("branch", "--show-current")
    current_branch = current_branch.strip()

    console.print(f"[bold cyan]Current branch:[/bold cyan] [bold green]{escape(current_branch)}[/bold green]")
    preview_cmd = "git --no-advice log -n 10 --oneline --color=always {1}"
    selected = fzf_select(branches, prompt="Select Branch to Switch To", preview=preview_cmd)
    if not selected:
        return

    target_branch = selected[0]
    if target_branch == current_branch:
        console.print(f"[bold yellow]⚠ Already on branch '{escape(target_branch)}'.[/bold yellow]")
        return

    try:
        run_git("switch", target_branch, capture=False, check=True)
        console.print(f"[bold green]✔ Successfully switched to branch '{escape(target_branch)}'.[/bold green]")
    except subprocess.CalledProcessError:
        console.print(f"[bold red]✖ Failed to switch to branch '{escape(target_branch)}'.[/bold red]")


def merge_branch() -> None:
    """Merges a selected branch into the current branch."""
    _, current_branch, _ = run_git("branch", "--show-current")
    current_branch = current_branch.strip()
    if not current_branch:
        console.print("[bold red]✖ Error: Detached HEAD state. Cannot merge.[/bold red]")
        return

    code, branches_out, _ = run_git("branch", "--format=%(refname:short)")
    if code != 0 or not branches_out.strip():
        console.print("[bold red]✖ Error: Failed to list local branches.[/bold red]")
        return

    other_branches = [b.strip() for b in branches_out.splitlines() if b.strip() and b.strip() != current_branch]
    if not other_branches:
        console.print("[bold yellow]⚠ No other local branches available to merge.[/bold yellow]")
        return

    preview_cmd = "git --no-advice log -n 10 --oneline --color=always {1}"
    console.print(f"[bold cyan]Merging into active branch:[/bold cyan] [bold green]{escape(current_branch)}[/bold green]")
    selected = fzf_select(other_branches, prompt="Select Branch to Merge IN", preview=preview_cmd)
    if not selected:
        return

    source_branch = selected[0]
    if not ask_yesno(f"Merge branch '{source_branch}' into '{current_branch}'?", default=True):
        console.print("[bold yellow]⚠ Merge operation cancelled.[/bold yellow]")
        return

    try:
        run_git("merge", source_branch, capture=False, check=True)
        console.print(f"[bold green]✔ Successfully merged branch '{escape(source_branch)}' into '{escape(current_branch)}'.[/bold green]")
    except subprocess.CalledProcessError:
        console.print("[bold red]✖ Merge failed or encountered conflicts. Resolve conflicts and commit.[/bold red]")


def push_branch_to_remote() -> None:
    """Pushes current or selected branch to remote origin and sets upstream."""
    _, current_branch, _ = run_git("branch", "--show-current")
    current_branch = current_branch.strip()
    if not current_branch:
        console.print("[bold red]✖ Error: Detached HEAD state. Cannot push branch.[/bold red]")
        return

    if ask_yesno(f"Push current branch '{current_branch}' to origin remote (set-upstream)?", default=True):
        safe_push(current_branch)


def delete_local_branch() -> None:
    """Deletes a local branch."""
    _, current_branch, _ = run_git("branch", "--show-current")
    current_branch = current_branch.strip()

    code, branches_out, _ = run_git("branch", "--format=%(refname:short)")
    if code != 0 or not branches_out.strip():
        console.print("[bold red]✖ Error: Failed to list local branches.[/bold red]")
        return

    other_branches = [b.strip() for b in branches_out.splitlines() if b.strip() and b.strip() != current_branch]
    if not other_branches:
        console.print("[bold yellow]⚠ No other local branches to delete.[/bold yellow]")
        return

    preview_cmd = "git --no-advice log -n 10 --oneline --color=always {1}"
    selected = fzf_select(other_branches, prompt="Select Local Branch to DELETE", preview=preview_cmd)
    if not selected:
        return

    target_branch = selected[0]
    if ask_yesno(f"Force delete local branch '{target_branch}'?", default=False):
        try:
            run_git("branch", "-D", target_branch, capture=False, check=True)
            console.print(f"[bold green]✔ Local branch '{target_branch}' deleted successfully.[/bold green]")
        except subprocess.CalledProcessError:
            console.print(f"[bold red]✖ Failed to delete local branch '{target_branch}'.[/bold red]")


def delete_remote_branch() -> None:
    """Deletes a remote branch on GitHub origin."""
    code, refs_out, _ = run_git("branch", "-r", "--format=%(refname:short)")
    if code != 0 or not refs_out.strip():
        console.print("[bold red]✖ Error: Failed to list remote branches.[/bold red]")
        return

    remote_branches = []
    for line in refs_out.splitlines():
        b = line.strip()
        if b.startswith("origin/") and not b.endswith("/HEAD"):
            remote_branches.append(b.removeprefix("origin/"))

    if not remote_branches:
        console.print("[bold yellow]⚠ No remote branches found.[/bold yellow]")
        return

    selected = fzf_select(remote_branches, prompt="Select Remote Branch on GitHub to DELETE")
    if not selected:
        return

    target_branch = selected[0]
    console.print(Panel.fit(
        f"[bold red]⚠ DELETE REMOTE BRANCH ⚠[/bold red]\n"
        f"This will permanently delete 'origin/{escape(target_branch)}' from GitHub remote repository!",
        border_style="red"
    ))
    if ask_yesno(f"Are you sure you want to delete 'origin/{target_branch}' on GitHub?", default=False):
        try:
            run_git("push", "origin", "--delete", target_branch, capture=False, check=True)
            console.print(f"[bold green]✔ Remote branch 'origin/{target_branch}' deleted successfully.[/bold green]")
        except subprocess.CalledProcessError:
            console.print(f"[bold red]✖ Failed to delete remote branch 'origin/{target_branch}'.[/bold red]")


def list_all_branches() -> None:
    """Displays detailed list of all local and remote branches."""
    console.print("\n[bold cyan]All Local & Remote Branches:[/bold cyan]")
    run_git("-c", "color.ui=always", "branch", "-a", "-v", capture=False)


def manage_branches() -> None:
    """Interactive sub-menu for complete branch management."""
    while True:
        _, current, _ = run_git("branch", "--show-current")
        current_str = current.strip() or "Detached HEAD"

        console.print(Panel.fit(
            f"[bold cyan]Active Branch:[/bold cyan] [bold green]{escape(current_str)}[/bold green]\n\n"
            "[bold cyan]1[/bold cyan] │ Create New Branch & Switch\n"
            "[bold cyan]2[/bold cyan] │ Switch Local Branch (FZF Picker & Commit Logs)\n"
            "[bold cyan]3[/bold cyan] │ Merge Branch into Active Branch\n"
            "[bold cyan]4[/bold cyan] │ Push Active Branch to Remote (origin set-upstream)\n"
            "[bold cyan]5[/bold cyan] │ Delete Local Branch\n"
            "[bold red]6[/bold red] │ Delete Remote Branch on GitHub (Destructive)\n"
            "[bold cyan]7[/bold cyan] │ List All Branches (Local & Remote)\n"
            "[bold red]q[/bold red] │ Return to Main Menu",
            title="[bold cyan]󰏖 BRANCH MANAGEMENT TOOLBOX[/bold cyan]",
            border_style="cyan",
            title_align="left",
            box=box.ROUNDED
        ))

        choice = ask().lower()
        if choice in ("q", "back", "exit"):
            break

        match choice:
            case "1": create_branch()
            case "2": switch_branch()
            case "3": merge_branch()
            case "4": push_branch_to_remote()
            case "5": delete_local_branch()
            case "6": delete_remote_branch()
            case "7": list_all_branches()
            case _: console.print("[bold red]✖ Invalid choice.[/bold red]")


def push_existing() -> None:
    """Option 4: pushes existing local commits on the current branch safely."""
    safe_push()


# --- 6. STASH MANAGEMENT ---
def get_stash_list() -> list[str]:
    """Retrieves list of stashes formatted for display and selection."""
    code, stash_out, _ = run_git("stash", "list")
    if code != 0 or not stash_out.strip():
        return []
    return [line.strip() for line in stash_out.splitlines() if line.strip()]


def list_stashes() -> None:
    """Lists current stashes."""
    stashes = get_stash_list()
    if not stashes:
        console.print("[bold yellow]⚠ No stashes found in repository.[/bold yellow]")
        return
    console.print("\n[bold cyan]Current Stashes:[/bold cyan]")
    for s in stashes:
        console.print(f"  [magenta]➔ {escape(s)}[/magenta]")


def create_stash() -> None:
    """Creates a stash with proper naming scheme: dusky-stash-YYYYMMDD-HHMMSS: description."""
    console.print("\n[bold cyan]Enter Stash Description / Label (or 'abort' to cancel):[/bold cyan]")
    desc = ask()
    if not desc or desc.lower() in ("abort", "q"):
        console.print("[bold yellow]⚠ Stash creation cancelled.[/bold yellow]")
        return

    timestamp = datetime.now().astimezone().strftime("%Y%m%d-%H%M%S")
    stash_msg = f"dusky-stash-{timestamp}: {desc}"

    include_untracked = ask_yesno("Include untracked files in stash?", default=False)

    stash_args = ["stash", "push", "-m", stash_msg]
    if include_untracked:
        stash_args.append("-u")

    before = len(get_stash_list())
    try:
        run_git(*stash_args, capture=False, check=True)
    except subprocess.CalledProcessError:
        console.print("[bold red]✖ Failed to create stash.[/bold red]")
        return

    if len(get_stash_list()) > before:
        console.print(f"[bold green]✔ Stash created successfully:[/bold green] [dim]{escape(stash_msg)}[/dim]")
    else:
        console.print("[bold yellow]⚠ Nothing to stash — working tree had no matching local changes.[/bold yellow]")


def pop_or_apply_stash(action: str = "pop") -> None:
    """Pops or applies a stash selected via FZF with diff preview."""
    stashes = get_stash_list()
    if not stashes:
        console.print("[bold yellow]⚠ No stashes found in repository.[/bold yellow]")
        return

    preview_cmd = "git --no-advice stash show -p $(printf %s {1} | cut -d: -f1)"
    prompt_text = f"Select Stash to {action.upper()}"

    selected = fzf_select(stashes, prompt=prompt_text, preview=preview_cmd)
    if not selected:
        return

    stash_ref = selected[0].split(":")[0].strip()

    try:
        run_git("stash", action, stash_ref, capture=False, check=True)
        msg = f"Successfully popped {stash_ref}." if action == "pop" else f"Successfully applied {stash_ref}."
        console.print(f"[bold green]✔ {msg}[/bold green]")
    except subprocess.CalledProcessError:
        _, conflict_out, _ = run_git("status", "--porcelain=v1")
        if any(e[:2] in ("UU", "AA", "DU", "UD", "AU", "UA", "DD") for e in conflict_out.splitlines()):
            console.print(f"[bold red]✖ Conflict while applying {stash_ref} — stash kept intact. Resolve, then drop manually.[/bold red]")
        else:
            console.print(f"[bold red]✖ Failed to {action} {stash_ref}.[/bold red]")


def drop_stash() -> None:
    """Drops/deletes a selected stash via FZF."""
    stashes = get_stash_list()
    if not stashes:
        console.print("[bold yellow]⚠ No stashes found in repository.[/bold yellow]")
        return

    preview_cmd = "git --no-advice stash show -p $(printf %s {1} | cut -d: -f1)"
    selected = fzf_select(stashes, prompt="Select Stash to DROP/DELETE", preview=preview_cmd)
    if not selected:
        return

    stash_ref = selected[0].split(":")[0].strip()
    stash_desc = selected[0]

    if ask_yesno(f"Permanently delete {stash_desc}?", default=False):
        try:
            run_git("stash", "drop", stash_ref, capture=False, check=True)
            console.print(f"[bold green]✔ Successfully dropped {stash_ref}.[/bold green]")
        except subprocess.CalledProcessError:
            console.print(f"[bold red]✖ Failed to drop {stash_ref}.[/bold red]")


def clear_stashes() -> None:
    """Clears all stashes in repository."""
    stashes = get_stash_list()
    if not stashes:
        console.print("[bold yellow]⚠ No stashes found in repository.[/bold yellow]")
        return

    console.print(Panel.fit(
        f"[bold red]⚠ CLEAR ALL STASHES ⚠[/bold red]\n"
        f"This will permanently delete all {len(stashes)} stash entry/entries!",
        border_style="red"
    ))
    if ask_yesno("Are you absolutely sure you want to clear ALL stashes?", default=False):
        try:
            run_git("stash", "clear", capture=False, check=True)
            console.print("[bold green]✔ All stashes cleared successfully.[/bold green]")
        except subprocess.CalledProcessError:
            console.print("[bold red]✖ Failed to clear stashes.[/bold red]")


def manage_stashes() -> None:
    """Interactive sub-menu for stash operations."""
    while True:
        console.print(Panel.fit(
            "[bold magenta]1[/bold magenta] │ Create Stash (With Timestamp & Custom Description)\n"
            "[bold magenta]2[/bold magenta] │ Pop Stash (Apply & remove from stash list)\n"
            "[bold magenta]3[/bold magenta] │ Apply Stash (Apply & keep in stash list)\n"
            "[bold magenta]4[/bold magenta] │ Drop / Delete Specific Stash\n"
            "[bold magenta]5[/bold magenta] │ List All Stashes\n"
            "[bold red]6[/bold red] │ Clear All Stashes (Destructive)\n"
            "[bold red]q[/bold red] │ Return to Main Menu",
            title="[bold magenta]󰏖 STASH MANAGEMENT TOOLBOX[/bold magenta]",
            border_style="magenta",
            title_align="left",
            box=box.ROUNDED
        ))

        choice = ask().lower()
        if choice in ("q", "back", "exit"):
            break

        match choice:
            case "1": create_stash()
            case "2": pop_or_apply_stash(action="pop")
            case "3": pop_or_apply_stash(action="apply")
            case "4": drop_stash()
            case "5": list_stashes()
            case "6": clear_stashes()
            case _: console.print("[bold red]✖ Invalid choice.[/bold red]")


# --- 7. ACTION REGISTRY (single source of truth for menus, help & dispatch) ---
@dataclass(frozen=True, slots=True)
class Action:
    key: str
    label: str
    category: int
    destructive: bool
    handler: Callable[[], None]


CATEGORIES: tuple[tuple[str, str], ...] = (
    ("STAGING & COMMITS (Local & Remote Sync)", "accent"),
    ("BRANCHING & REMOTE PRs (GitHub PRs & Branch Management)", "success"),
    ("STASH & WORKSPACE EDITS (Stashes & Discard Edits)", "warning"),
    ("LOCAL HISTORY RECOVERY (Safe Undos & Local Rollbacks)", "accent"),
    ("DESTRUCTIVE FORCE REWRITING (History Obliteration)", "error"),
)

ACTIONS: tuple[Action, ...] = (
    Action("1",  "Commit All (Local & Remote)",                                              0, False, lambda: sync_all()),
    Action("2",  "Commit Specific File(s) (Local & Remote)",                                 0, False, sync_single),
    Action("3",  "Commit All (Local Only)",                                                  0, False, lambda: sync_all(local_only=True)),
    Action("4",  "Push Existing Local Commits to Remote",                                    0, False, push_existing),
    Action("5",  "View Delta Differential",                                                  0, False, show_delta),
    Action("6",  "Checkout GitHub Pull Request (Local Inspection/Edits)",                    1, False, checkout_pr),
    Action("7",  "Branch Management Submenu (Create / Switch / Merge / Push / Delete)",      1, False, manage_branches),
    Action("8",  "Stash Management Submenu (Create / Pop / Apply / Drop / Clear)",           2, False, manage_stashes),
    Action("9",  "Discard All Uncommitted Local Edits",                                      2, True,  discard_local_changes),
    Action("10", "Undo Last Commit Safely (Creates Revert Commit on Local & Remote)",        3, False, safe_revert_last_commit),
    Action("11", "Undo Local Commits to a Specific Commit (Safe - keeps edits on disk)",     3, False, undo_local_commits_to_commit),
    Action("12", "Reset Local Branch State to Match GitHub",                                 3, True,  reset_local_to_remote),
    Action("13", "Delete Local Commits since a Specific Commit",                             4, True,  delete_local_commits_to_commit),
    Action("14", "Delete Last Commit from Remote (Force Push HEAD~1)",                       4, True,  quick_step_back),
    Action("15", "Delete Commits since a Specific Commit from Remote (Nuclear Force Push)",  4, True,  nuclear_revert),
    Action("16", "Engage Ephemeral Time Machine (TUI)",                                      4, False, run_time_machine),
)

ACTION_MAP: dict[str, Action] = {a.key: a for a in ACTIONS}
VALID_KEYS: frozenset[str] = frozenset(ACTION_MAP)


def print_help() -> None:
    """Prints a categorized, color-coded usage manual of CLI quick flags."""
    console.print(f"\n[bold blue]󰏖 Dusky CLI Quick Help[/bold blue]  [dim]v{DUSKY_VERSION}[/dim]")
    console.print(f"Usage: [bold green]dusky {escape('[option]')}[/bold green]")
    console.print("If no option is provided, the interactive dashboard is opened.\n")

    table = Table(box=box.ROUNDED, show_header=True, header_style="bold white")
    table.add_column("Option", style="bold", width=8, justify="center")
    table.add_column("Action", style="white")
    table.add_column("Destructive", justify="center")

    for idx, (title, colorkey) in enumerate(CATEGORIES):
        c = COLORS.get(colorkey, "white")
        table.add_row(
            f"[bold {c}]Category[/bold {c}]",
            f"[bold {c}]  {title}[/bold {c}]",
            "",
        )
        for action in ACTIONS:
            if action.category != idx:
                continue
            mark = "[bold red]YES[/bold red]" if action.destructive else "[green]No[/green]"
            table.add_row(f"[bold {c}]{action.key}[/bold {c}]", f"[{c}]{escape(action.label)}[/{c}]", mark)
        if idx < len(CATEGORIES) - 1:
            table.add_section()

    table.add_row("[bold red]q[/bold red]", "[red]Quit Dashboard[/red]", "[green]No[/green]")
    table.add_row("[bold red]h[/bold red]", "[red]Show this CLI help menu[/red]", "[green]No[/green]")

    console.print(table)


def render_dashboard() -> None:
    """Renders the categorized dashboard panels straight from the ACTION registry."""
    _, current_branch_raw, _ = run_git("branch", "--show-current")
    active_branch = current_branch_raw.strip() or "Detached HEAD"

    c_acc = COLORS["accent"]
    c_suc = COLORS["success"]

    console.print(
        f"[bold {c_acc}]󰏖 Dusky Dotfiles Manager[/bold {c_acc}]  │  "
        f"[bold {c_acc}]Active Branch:[/bold {c_acc}] [bold {c_suc}]{escape(active_branch)}[/bold {c_suc}]\n"
    )

    for idx, (title, colorkey) in enumerate(CATEGORIES):
        c = COLORS.get(colorkey, "white")
        rows = [
            f"[bold {c}]{a.key}[/bold {c}] │ "
            + (f"[bold {COLORS['error']}]{escape(a.label)}[/bold {COLORS['error']}]" if a.destructive else escape(a.label))
            for a in ACTIONS
            if a.category == idx
        ]
        if idx == len(CATEGORIES) - 1:
            c_err = COLORS["error"]
            rows.append(f"[bold {c_err}]q[/bold {c_err}] │ Quit Dashboard")
        console.print(Panel.fit(
            "\n".join(rows),
            title=f"[bold {c}]  {title}[/bold {c}]",
            border_style=c,
            title_align="left",
            box=box.ROUNDED
        ))


# --- 8. MAIN ROUTING ENGINE ---
def dispatch(choice: str) -> None:
    """Executes a registry action by key."""
    try:
        if choice == "16":
            ACTION_MAP[choice].handler()
            return
        # Share the same advisory lock as Time Machine so our own tools cannot
        # stage/reset the work tree while the other tool is switching snapshots.
        lock_dir = GIT_DIR / "dusky-time-machine"
        lock_dir.mkdir(mode=0o700, exist_ok=True)
        with (lock_dir / "worktree.lock").open("a") as lock:
            try:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                console.print("[yellow]Another Dusky operation is using this repository.[/yellow]")
                return
            ACTION_MAP[choice].handler()
    except (subprocess.CalledProcessError, OSError, ValueError, RuntimeError) as exc:
        console.print(f"[red]✖ Operation stopped: {escape(str(exc))}[/red]")


def main() -> Never:
    if len(sys.argv) > 1:
        choice = sys.argv[1].strip()
        if choice in ("-h", "--help", "help", "h"):
            print_help()
            sys.exit(0)
        if choice in ("-V", "--version", "version"):
            console.print(f"dusky v{DUSKY_VERSION}")
            sys.exit(0)
        if choice in VALID_KEYS:
            check_dependencies()
            dispatch(choice)
            sys.exit(0)
        console.print(f"[bold red]✖ Invalid choice argument '{escape(choice)}'.[/bold red]")
        print_help()
        sys.exit(1)

    check_dependencies()
    while True:
        console.clear()
        render_dashboard()

        console.print(f"\n[bold {COLORS['accent']}]Awaiting Directive [1-{len(ACTIONS)}/q][/bold {COLORS['accent']}]")
        choice = ask()
        while choice not in VALID_KEYS and choice != "q":
            if not choice:
                console.print("[bold yellow]⚠ No default action — enter a number explicitly (safety first).[/bold yellow]")
            else:
                console.print("[bold red]✖ Invalid choice. Please select a valid key.[/bold red]")
            choice = ask()

        if choice == "q":
            raise SystemExit(0)

        dispatch(choice)

        console.print("\n[dim]Press [Enter] to return to dashboard...[/dim]")
        ask()


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        console.print("\n[bold yellow]⚠ Execution Terminated.[/bold yellow]")
        sys.exit(0)
