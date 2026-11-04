#!/usr/bin/env python3
"""
Dusky Dotfiles Manager — bare repository at $HOME/dusky, work tree at $HOME.

Manifest entries scope commits and discards; they never implicitly untrack files.
Selected commits include the current contents of selected paths, including both
sides of renames, while preserving unrelated index entries. Missing manifests
make Commit All update tracked files only; an empty manifest selects nothing.
Interactive prompts use Readline; selectors use the full terminal.
"""

import fcntl
import fnmatch
import json
import os
import re
import shlex
import shutil
import subprocess
import sys
import tempfile
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from itertools import batched
from pathlib import Path, PurePosixPath
from typing import Any, Never

# Fast-path detection for high-frequency fzf helper callbacks.
# Skips importing `rich` and `readline` on preview/keybind invocations while
# keeping all module attributes eagerly initialized on normal runs and test imports.
_FAST_CLI_FLAGS: frozenset[str] = frozenset({
    "--resize-preview",
    "--move-preview",
    "--toggle-vim",
    "--vim-init",
    "--key-escape",
    "--diff-preview",
    "--diff-preview-json",
})
_IS_FAST_CLI: bool = (
    __name__ == "__main__" and len(sys.argv) > 1 and sys.argv[1] in _FAST_CLI_FLAGS
)

if not _IS_FAST_CLI:
    import readline  # noqa: F401 — Enable cursor movement, Home/End and deletion in input().
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
else:
    box = None  # type: ignore[assignment]
    Console = None  # type: ignore[assignment,misc]
    Panel = None  # type: ignore[assignment,misc]
    Table = None  # type: ignore[assignment,misc]

    def escape(text: str) -> str:
        return text


# --- 1. CONSTANTS, STATE & TYPE ALIASES (PEP 695) ---
HOME: Path = Path.home()
GIT_DIR: Path = Path(os.environ.get("DUSKY_GIT_DIR", HOME / "dusky")).expanduser().absolute()
WORK_TREE: Path = HOME
DOTFILES_LIST: Path = Path(os.environ.get("DUSKY_GIT_LIST", HOME / ".git_dusky_list")).expanduser().absolute()
TIME_MACHINE_BIN: Path = Path(__file__).resolve().parent / "time_machine" / "dusky_time_machine_tui.sh"
MATUGEN_JSON: Path = HOME / ".config" / "matugen" / "generated" / "dusky_tui.json"

DUSKY_VERSION: str = "13.2.0"

type GitResult = tuple[int, str, str]
type ChangedEntry = tuple[str, str | None, str]

ANSI_RE: re.Pattern[str] = re.compile(r"\x1b\[[0-9;]*m")
CONFLICT_STATUSES: frozenset[str] = frozenset({"DD", "AU", "UD", "UA", "DU", "AA", "UU"})
GIT_ENV_OVERRIDES: tuple[str, ...] = (
    "GIT_INDEX_FILE",
    "GIT_COMMON_DIR",
    "GIT_OBJECT_DIRECTORY",
    "GIT_ALTERNATE_OBJECT_DIRECTORIES",
    "GIT_PREFIX",
    "GIT_LITERAL_PATHSPECS",
    "GIT_GLOB_PATHSPECS",
    "GIT_NOGLOB_PATHSPECS",
    "GIT_ICASE_PATHSPECS",
)

SETTINGS_DIR: Path = HOME / ".config" / "dusky" / "settings"
LAYOUT_FILE: Path = SETTINGS_DIR / "git_preview_layout"
VIM_MODE_FILE: Path = SETTINGS_DIR / "git_vim_mode"

VIM_KEYS: str = "j,k,g,G,J,K,v,V,q,ctrl-a,ctrl-d,ctrl-u,/"
PROMPT_VIM: str = " 🅝 q:quit /:search ❯ "


def get_preview_layout() -> tuple[str, int, str]:
    """Retrieves current preview layout preference (edge, percentage, rest)."""
    cur = "right,70%,border-left,wrap"
    if LAYOUT_FILE.is_file():
        try:
            val = LAYOUT_FILE.read_text(encoding="utf-8").strip()
            if val and (val == "hidden" or "," in val):
                cur = val
        except OSError:
            pass
    if cur == "hidden":
        return "hidden", 70, "wrap"
    parts = cur.split(",")
    edge = parts[0] if parts else "right"
    pct = 70
    if len(parts) > 1 and parts[1].endswith("%"):
        try:
            pct = int(parts[1][:-1])
        except ValueError:
            pct = 70
    rest = ",".join(parts[2:]) if len(parts) > 2 else "border-left,wrap"
    return edge, pct, rest


def handle_fzf_layout(action: str, direction: str) -> None:
    """Compatibility entry point; fzf calls the lightweight Bash helper directly."""
    subprocess.run(
        ["bash", str(Path(__file__).resolve().with_name("git_fzf_layout.sh")),
         action, direction, str(SETTINGS_DIR)],
        check=True,
    )


def handle_fzf_toggle_vim(default_prompt: str = "Select") -> None:
    """Toggles modal Vim navigation keys (Alt-M) in FZF."""
    try:
        SETTINGS_DIR.mkdir(parents=True, exist_ok=True)
    except OSError:
        pass
    is_vim = True
    if VIM_MODE_FILE.is_file():
        try:
            is_vim = VIM_MODE_FILE.read_text(encoding="utf-8").strip() != "false"
        except OSError:
            pass
    next_vim = not is_vim
    try:
        VIM_MODE_FILE.write_text("true" if next_vim else "false", encoding="utf-8")
    except OSError:
        pass

    if next_vim:
        print(f"rebind({VIM_KEYS})+disable-search+change-prompt({PROMPT_VIM})+refresh-preview")
    else:
        prompt_str = default_prompt if default_prompt.endswith("❯ ") else f"{default_prompt} ❯ "
        print(f"unbind(j,k,g,G,J,K,v,V,q,/)+enable-search+change-prompt({prompt_str})+refresh-preview")


def handle_fzf_vim_init(default_prompt: str = "") -> None:
    """Initializes FZF binding state according to user's saved Vim mode preference."""
    is_vim = True
    if VIM_MODE_FILE.is_file():
        try:
            is_vim = VIM_MODE_FILE.read_text(encoding="utf-8").strip() != "false"
        except OSError:
            pass
    if is_vim:
        print(f"rebind({VIM_KEYS})+disable-search+change-prompt({PROMPT_VIM})+refresh-preview")
    else:
        prompt_act = f"+change-prompt({default_prompt})" if default_prompt else ""
        print(f"unbind(j,k,g,G,J,K,v,V,q,/)+enable-search{prompt_act}+refresh-preview")


def handle_fzf_escape() -> None:
    """Context-sensitive Esc handling: leaves search mode back to Vim, or aborts."""
    prompt = os.environ.get("FZF_PROMPT", "")
    if "󰍉" in prompt:
        print(f"rebind({VIM_KEYS})+disable-search+change-prompt({PROMPT_VIM})")
    else:
        print("abort")


def handle_diff_preview(target: str, cur: str, selected: list[str]) -> None:
    """Renders live diff preview for hovered or selected files with Delta integration."""
    if not cur and not selected:
        return

    # If hovering over an unselected file, display that hovered file.
    # If hovering over a selected file (or if nothing hovered), display all selected files.
    if selected and cur and cur not in selected:
        files = [cur]
    elif selected:
        files = selected
    elif cur:
        files = [cur]
    else:
        return

    files = list(dict.fromkeys(f for f in files if f))
    if not files:
        return

    def new_file_diff(path: str) -> str:
        full_path = WORK_TREE / path
        if not full_path.exists() and not full_path.is_symlink():
            return ""
        # --no-index treats a directory symlink as a directory and tries to
        # compare a nonexistent child named "null". Show its target instead.
        if full_path.is_symlink() and full_path.is_dir():
            return f"New symlink: {path!r} → {os.readlink(full_path)!r}\n"
        if full_path.is_dir():
            return f"Untracked directory: {path!r}\n"
        code, out, err = run_git(
            "diff", "--color=always", "--no-index", "--", "/dev/null", str(full_path)
        )
        # --no-index implies --exit-code: 1 normally means a difference, but
        # Git also uses it for some read errors without producing a diff.
        if code not in (0, 1) or (code == 1 and err and not out):
            raise RuntimeError(f"File preview failed: {err.strip()}")
        return out

    diff_outputs: list[str] = []
    if target:
        _, out, _ = run_git(
            "show", "--color=always", "--patch", "--format=",
            target, "--", *files, literal_pathspecs=True, check=True,
        )
        diff_outputs.append(out)
    elif run_git("rev-parse", "--verify", "HEAD")[0] == 0:
        # One diff includes tracked edits and staged additions, with Git's own
        # handling of renames, symlinks and deleted files.
        _, out, _ = run_git(
            "diff", "--color=always", "--patch", "HEAD", "--",
            *files, literal_pathspecs=True, check=True,
        )
        diff_outputs.append(out)
        _, out, _ = run_git(
            "ls-files", "--others", "--exclude-standard", "-z",
            "--", *files, literal_pathspecs=True, check=True,
        )
        new_files = [f for f in out.split("\0") if f]
        for f in new_files:
            diff_outputs.append(new_file_diff(f))
    else:
        # An unborn branch has no base tree. Show the current disk contents,
        # including unstaged edits to newly staged files.
        for f in files:
            full_path = WORK_TREE / f
            if full_path.exists() or full_path.is_symlink():
                diff_outputs.append(new_file_diff(f))

    raw_diff = "".join(diff_outputs)
    if not raw_diff:
        return

    delta_bin = shutil.which("delta")
    try:
        if delta_bin:
            cols = os.environ.get("FZF_PREVIEW_COLUMNS")
            delta_cmd = [delta_bin, "--paging=never"]
            if cols and cols.isdigit():
                delta_cmd.append(f"--width={cols}")
            proc = subprocess.run(
                delta_cmd, input=raw_diff, text=True, errors="surrogateescape", check=False
            )
            # Ignore SIGPIPE (-13 or 141) when fzf closes the preview pipe early.
            if proc.returncode not in (0, -13, 141):
                raise subprocess.CalledProcessError(proc.returncode, delta_cmd)
        else:
            sys.stdout.write(raw_diff)
            sys.stdout.flush()
    except BrokenPipeError:
        pass


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
    if not _IS_FAST_CLI and MATUGEN_JSON.is_file():
        try:
            data = json.loads(MATUGEN_JSON.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                valid = {
                    k: v for k, v in data.items()
                    if k in defaults and isinstance(v, str)
                    and re.fullmatch(r"#[0-9a-fA-F]{6}", v)
                }
                return defaults | valid
        except (json.JSONDecodeError, UnicodeError, OSError):
            pass
    return defaults


COLORS: dict[str, str] = load_matugen_colors()

# Set Git environment variables globally so child processes (like fzf and sub-scripts)
# execute within the correct bare repository context.
os.environ["GIT_DIR"] = str(GIT_DIR)
os.environ["GIT_WORK_TREE"] = str(WORK_TREE)

console: Any = Console() if not _IS_FAST_CLI else None


def _print_err(markup_msg: str) -> None:
    """Lazily initializes Console if an error occurs on the fast-CLI path."""
    global console
    if console is None:
        from rich.console import Console as _Console
        console = _Console()
    console.print(markup_msg)


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

    # Pin every Git command to this repository and work tree.
    git_env["GIT_WORK_TREE"] = str(WORK_TREE)
    git_env["GIT_DIR"] = str(GIT_DIR)

    # Inherited alternate indexes/pathspec modes must not redirect these actions.
    for key in GIT_ENV_OVERRIDES:
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
        *args,
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
        if capture:
            details = "\n".join(
                stream.decode("utf-8", errors="replace").strip()
                for stream in (proc.stdout, proc.stderr)
                if stream and stream.strip()
            )
            if details:
                from rich.markup import escape as _escape
                _print_err("[bold red]Git Internal Error:[/bold red]\n" + _escape(details))
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
    """Evaluates Git-style globs (including **) and exact boundaries purely in Python.

    A manifest entry of "." means the entire work tree. Paths inside GIT_DIR
    are always excluded, and compiler caches are ignored by policy.
    """
    if not path or is_internal_gitdir(path):
        return False

    posix_path = PurePosixPath(path)
    # Explicitly ignore compiler cache folders/files
    if "__pycache__" in posix_path.parts or path.endswith((".pyc", ".pyo", ".pyd")):
        return False

    for vp in valid_paths:
        vp_clean = vp.rstrip("/")
        if vp_clean == ".":
            return True
        # Exact match or Directory prefix match
        if path == vp_clean or path.startswith(vp_clean + "/"):
            return True
        # Glob match (e.g., *.conf, **/*.sh)
        if (
            fnmatch.fnmatch(path, vp_clean)
            or fnmatch.fnmatch(path, vp_clean + "/*")
            or (
                any(ch in vp_clean for ch in "*?[")
                and (posix_path.full_match(vp_clean) or posix_path.full_match(f"{vp_clean}/**"))
            )
        ):
            return True

    return False


def get_list_pathspecs() -> list[str] | None:
    """Extracts valid paths ensuring boundary limitations to $HOME.

    Returns None when the manifest file is missing (blanket mode),
    otherwise the deduplicated list (possibly empty).
    """
    if not DOTFILES_LIST.is_file():
        return None

    raw_lines = DOTFILES_LIST.read_text(encoding="utf-8", errors="surrogateescape").splitlines()
    valid_paths: list[str] = []
    seen: set[str] = set()

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
                if "." not in seen:
                    seen.add(".")
                    valid_paths.append(".")
                continue
            if normalized_abs.is_relative_to(WORK_TREE):
                rel_str = str(normalized_abs.relative_to(WORK_TREE))
                if is_internal_gitdir(rel_str):
                    console.print(
                        f"[bold red]✖ Security Block:[/bold red] "
                        f"path inside GIT_DIR ignored -> {escape(clean)}"
                    )
                    continue
                if rel_str not in seen:
                    seen.add(rel_str)
                    valid_paths.append(rel_str)
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
) -> list[ChangedEntry]:
    """Read NUL-delimited status, limiting filesystem scans to deduplicated manifest roots."""
    if scope == []:
        return []
    roots: set[str] = set()
    for pattern in scope or []:
        # Scan a literal ancestor of a glob, then use one matching policy below.
        wildcard = re.search(r"[?*\[]", pattern)
        if wildcard:
            prefix = pattern[:wildcard.start()]
            root = prefix.rsplit("/", 1)[0] if "/" in prefix else "."
        else:
            root = pattern.rstrip("/")
        roots.add(root or ".")
    if "." in roots:
        roots.clear()

    # Prune child roots already covered by an ancestor directory in `roots`.
    pruned_roots: list[str] = []
    for r in sorted(roots):
        if not any(r == anc or r.startswith(anc + "/") for anc in pruned_roots):
            pruned_roots.append(r)

    paths = [f":(top,literal){root}" for root in pruned_roots]
    batches = [list(b) for b in batched(paths, 128)] if paths else [[]]
    # Path-limited status loses the other side of cross-scope renames.
    # Scan tracked entries together; constrain only the untracked traversal.
    _, out, _ = run_git(
        "status", "--porcelain=v1", "-z", "--renames", "--untracked-files=no", check=True
    )
    result: dict[ChangedEntry, None] = {}
    entries = iter(out.split("\0"))
    for entry in entries:
        if not entry:
            continue
        status, path = entry[:2], entry[3:]
        old = next(entries) if "R" in status or "C" in status else None
        if not is_internal_gitdir(path) and not (old and is_internal_gitdir(old)):
            result[(path, old, status)] = None
    if not tracked_only:
        for batch in batches:
            _, out, _ = run_git(
                "ls-files", "--others", "--exclude-standard", "-z", "--", *batch, check=True
            )
            for path in out.split("\0"):
                if path and not is_internal_gitdir(path):
                    result[(path, None, "??")] = None
    return list(result)


def stage_entries(entries: list[ChangedEntry], *, local_only: bool = False) -> bool | None:
    """Stage selected work-tree changes; leave unrelated staged files alone."""
    if any(status in CONFLICT_STATUSES for _, _, status in entries):
        console.print("[bold red]✖ Resolve merge conflicts before selecting files to commit.[/bold red]")
        return False
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
        run_git(
            "add", "--all", "--pathspec-from-file=-", "--pathspec-file-nul",
            input_data=payload, literal_pathspecs=True, check=True,
        )
    if selected:
        return commit_and_push(sorted(selected), local_only=local_only)
    console.print("[green]No matching changes to commit.[/green]")
    return None


def scoped_entries(*, tracked_only: bool = False) -> list[ChangedEntry]:
    paths = get_list_pathspecs()
    if paths == []:
        console.print("[yellow]The manifest has no valid paths; nothing selected.[/yellow]")
        return []
    return [
        entry for entry in changed_entries(tracked_only=tracked_only, scope=paths)
        if paths is None or matches_pathspec(entry[0], paths) or matches_pathspec(entry[1], paths)
    ]


def sync_all(local_only: bool = False) -> bool | None:
    """Commit matching changes without implicitly untracking other files."""
    return stage_entries(
        scoped_entries(tracked_only=not DOTFILES_LIST.is_file()), local_only=local_only
    )


DIR_PALETTE: list[str] = [
    "\033[38;5;111m",  # Sky blue
    "\033[38;5;176m",  # Lavender
    "\033[38;5;215m",  # Peach
    "\033[38;5;114m",  # Sage green
    "\033[38;5;221m",  # Warm yellow
    "\033[38;5;116m",  # Mint teal
    "\033[38;5;211m",  # Soft rose
    "\033[38;5;147m",  # Periwinkle
    "\033[38;5;180m",  # Sand
    "\033[38;5;152m",  # Ice blue
    "\033[38;5;182m",  # Mauve
    "\033[38;5;186m",  # Light olive
]


def format_path_colored(path: str, dir_color_cache: dict[str, str]) -> str:
    """Match gitdelta's palette: one color per first two directory components."""
    parts = path.split("/")
    if len(parts) == 1:
        return path

    if len(parts) <= 2:
        base_dir = parts[0] + "/"
        sub_dir = ""
        file_name = parts[1]
    else:
        base_dir = f"{parts[0]}/{parts[1]}/"
        sub_dir = "/".join(parts[2:-1]) + "/" if len(parts) > 3 else ""
        file_name = parts[-1]

    if base_dir not in dir_color_cache:
        dir_color_cache[base_dir] = DIR_PALETTE[len(dir_color_cache) % len(DIR_PALETTE)]

    c_base = dir_color_cache[base_dir]
    if sub_dir:
        return f"{c_base}{base_dir}\033[2m{sub_dir}\033[0m{file_name}"
    return f"{c_base}{base_dir}\033[0m{file_name}"


def get_commit_preview_cmd() -> str:
    """Constructs preview command for commit hashes, using delta if available."""
    delta_bin = shutil.which("delta")
    if delta_bin:
        return (
            f"git --no-advice show --color=always {{1}} | "
            f"{shlex.quote(delta_bin)} --paging=never ${{FZF_PREVIEW_COLUMNS:+--width=$FZF_PREVIEW_COLUMNS}}"
        )
    return "git --no-advice show --color=always {1}"


def get_stash_preview_cmd() -> str:
    """Constructs preview command for git stash entries, using delta if available."""
    delta_bin = shutil.which("delta")
    if delta_bin:
        return (
            f"git --no-advice stash show -p --include-untracked --color=always "
            f"$(printf %s {{1}} | cut -d: -f1) | "
            f"{shlex.quote(delta_bin)} --paging=never ${{FZF_PREVIEW_COLUMNS:+--width=$FZF_PREVIEW_COLUMNS}}"
        )
    return "git --no-advice stash show -p --include-untracked --color=always $(printf %s {1} | cut -d: -f1)"


def get_numstat_map() -> dict[str, str]:
    """Returns a mapping of relative file paths to colorized (+a, -d) diff badges."""
    run_git("update-index", "--refresh", "-q")
    base = ["HEAD"] if run_git("rev-parse", "--verify", "HEAD")[0] == 0 else ["--cached"]
    _, numstat_out, _ = run_git("diff", "--numstat", "-z", *base, check=True)
    res: dict[str, str] = {}
    records = iter(numstat_out.split("\0"))
    for record in records:
        if not record:
            continue
        add, delete, path = record.split("\t", 2)
        if not path:  # Renames have two separate NUL-terminated paths.
            next(records)
            path = next(records)
        if add == "-" and delete == "-":
            res[path] = "\033[90m(binary)\033[0m"
        else:
            parts_fmt = []
            if int(add):
                parts_fmt.append(f"\033[32m+{add}\033[0m")
            if int(delete):
                parts_fmt.append(f"\033[31m-{delete}\033[0m")
            if parts_fmt:
                res[path] = " ".join(parts_fmt)
    return res


def reconcile_reverted_staged_entries() -> None:
    """Re-sync staged entries that were subsequently reverted on disk back to HEAD.

    Only updates scoped entries whose current disk state matches HEAD (or was
    removed on an unborn branch), preserving unrelated index entries and keeping
    genuine [MM]/[AM] badges intact for the interactive selector.
    """
    paths = get_list_pathspecs()
    if paths == []:
        return
    run_git("update-index", "--refresh", "-q")
    _, pre_out, _ = run_git(
        "status", "--porcelain=v1", "-z", "--renames", "--untracked-files=no", check=True
    )
    stale_candidates: list[str] = []
    pre_it = iter(pre_out.split("\0"))
    for entry in pre_it:
        if not entry:
            continue
        st, p = entry[:2], entry[3:]
        old_p = next(pre_it) if "R" in st or "C" in st else None
        if st in CONFLICT_STATUSES:
            continue
        if st[0] not in (" ", "?", "!") and st[1] != " ":
            if paths is None or matches_pathspec(p, paths) or matches_pathspec(old_p, paths):
                stale_candidates.append(p)
                if old_p and st[1] == "R":
                    stale_candidates.append(old_p)

    if not stale_candidates:
        return

    unique_candidates = sorted(set(stale_candidates))
    payload = os.fsencode("\0".join(unique_candidates) + "\0")
    if run_git("rev-parse", "--verify", "HEAD")[0] == 0:
        _, still_diff_out, _ = run_git(
            "diff", "--name-only", "-z", "HEAD",
            "--pathspec-from-file=-", "--pathspec-file-nul",
            input_data=payload, literal_pathspecs=True, check=True,
        )
        still_dirty = {p for p in still_diff_out.split("\0") if p}
        reverted = [p for p in unique_candidates if p not in still_dirty]
    else:
        reverted = [
            p for p in unique_candidates
            if not (WORK_TREE / p).exists() and not (WORK_TREE / p).is_symlink()
        ]

    if reverted:
        rev_payload = os.fsencode("\0".join(reverted) + "\0")
        run_git(
            "add", "--all", "--pathspec-from-file=-", "--pathspec-file-nul",
            input_data=rev_payload, literal_pathspecs=True, check=True,
        )


def sync_single() -> bool | None:
    """Stage and commit selected changes."""
    reconcile_reverted_staged_entries()

    entries = scoped_entries()
    if not entries:
        console.print("[green]No matching changes to commit.[/green]")
        return None

    selected = select_changed_entries(entries, prompt="Stage Files")
    if selected:
        return stage_entries(selected)
    return None


def select_changed_entries(
    entries: list[ChangedEntry], *, prompt: str, notice: str = "",
) -> list[ChangedEntry]:
    """Select literal filenames with live Delta preview and colorized hierarchy."""
    numstat_map = get_numstat_map()
    dir_colors: dict[str, str] = {}
    choices: dict[str, list[ChangedEntry]] = {}
    displays: list[str] = []

    for number, entry in enumerate(entries, 1):
        path, old, status = entry
        badge = format_status_badge(status)
        display_path = repr(path)[1:-1] if any(ord(c) < 32 for c in path) else path
        colored_path = format_path_colored(display_path, dir_colors)
        stat = numstat_map.get(path, "")
        stat_suffix = f"  {stat}" if stat else ""
        old_suffix = f" (from {repr(old)[1:-1]})" if old else ""
        display = f"{number:2d}: {badge} {colored_path}{old_suffix}{stat_suffix}"
        identity = json.dumps(path, ensure_ascii=True)
        choices.setdefault(identity, []).append(entry)
        displays.append(f"{identity}\t{display}")

    self_bin = shlex.quote(str(Path(__file__).resolve()))
    preview_cmd = f"{self_bin} --diff-preview-json '' {{1}} {{+1}}"

    header = (
        " \033[90m[TAB]\033[0m Mark  \033[90m[Alt-M]\033[0m Vim  \033[90m[Alt-←/→]\033[0m Resize  "
        "\033[90m[Alt-H/J/K/L]\033[0m Move  \033[90m[Alt-V]\033[0m View  \033[90m[Shift-↑/↓]\033[0m Scroll │  "
        "\033[33m[ M]\033[0m Mod  \033[32m[M ]\033[0m Staged  \033[35m[MM]\033[0m Both  "
        "\033[31m[ D]\033[0m Del  \033[36m[A ]\033[0m Add  \033[90m[??]\033[0m New"
    )
    if notice:
        header = f"{notice}\n{header}"

    selected = fzf_select(
        displays,
        prompt=prompt,
        multi=True,
        preview=preview_cmd,
        delimiter="\t",
        with_nth="2..",
        header=header,
        preview_label=" file diff ",
    )

    seen_identities: set[str] = set()
    result: list[ChangedEntry] = []
    for line in selected or []:
        identity = line.split("\t", 1)[0]
        if identity in choices and identity not in seen_identities:
            seen_identities.add(identity)
            result.extend(choices[identity])
    return result


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
    header: str | None = None,
    delimiter: str | None = None,
    with_nth: str | None = None,
    preview_window: str | None = None,
    preview_label: str | None = None,
) -> list[str]:
    """Feeds NUL-terminated strings to FZF safely via synchronous PIPEs."""
    if not choices:
        return []

    self_bin = shlex.quote(str(Path(__file__).resolve()))
    prompt_escaped = shlex.quote(prompt + " ❯ ")

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
        "--with-shell=/bin/sh -c",
        f"--color={fzf_colors}",
        f"--prompt={prompt} ❯ ",
        "--pointer=❯ ",
        "--marker=✔ ",
        "--info=inline",
        "--no-height",
        "--layout=reverse",
        "--border=rounded",
        "--ellipsis=",
        "--highlight-line",
        "--scrollbar=│",
        "--separator=─",
    ]
    if delimiter:
        fzf_cmd.append(f"--delimiter={delimiter}")
    if with_nth:
        fzf_cmd.append(f"--with-nth={with_nth}")

    if header:
        fzf_cmd.append(f"--header={header}")
    elif multi:
        default_header = (
            " \033[90m[TAB]\033[0m Mark  \033[90m[Alt-M]\033[0m Vim  \033[90m[Alt-←/→]\033[0m Resize  "
            "\033[90m[Alt-H/J/K/L]\033[0m Move  \033[90m[Alt-V]\033[0m View  \033[90m[ENTER]\033[0m Confirm"
        )
        fzf_cmd.append(f"--header={default_header}")

    bind_actions: list[str] = [
        f"alt-m:transform:{self_bin} --toggle-vim {prompt_escaped}",
        f"esc:transform:{self_bin} --key-escape",
        f"start:transform:{self_bin} --vim-init {prompt_escaped}",
        "j:down", "k:up", "g:first", "G:last",
        "ctrl-u:half-page-up",
        "q:abort",
        f"/:change-prompt( 󰍉 search ❯ )+enable-search+unbind({VIM_KEYS})",
    ]

    if multi:
        fzf_cmd.append("--multi")
        bind_actions.extend([
            "ctrl-a:select-all", "ctrl-d:deselect-all",
            "J:toggle+down", "K:toggle+up", "v:toggle", "V:toggle",
        ])
    else:
        bind_actions.append("ctrl-d:half-page-down")

    if preview:
        fzf_cmd.extend(["--preview", preview])
        if not preview_window:
            edge, pct, _ = get_preview_layout()
            border = {
                "left": "border-right",
                "right": "border-left",
                "up": "border-bottom",
                "down": "border-top",
            }.get(edge, "border-left")
            win = f"{edge}:{pct}%:{border}:wrap" if edge != "hidden" else "hidden"
        else:
            win = preview_window
        fzf_cmd.append(f"--preview-window={win}")
        if preview_label:
            fzf_cmd.extend([f"--preview-label={preview_label}", "--preview-label-pos=center"])
        layout_bin = shlex.quote(str(Path(__file__).resolve().with_name("git_fzf_layout.sh")))
        bind_actions.extend([
            f"alt-left:transform:bash {layout_bin} --resize-preview left",
            f"alt-right:transform:bash {layout_bin} --resize-preview right",
            f"alt-up:transform:bash {layout_bin} --resize-preview up",
            f"alt-down:transform:bash {layout_bin} --resize-preview down",
            f"alt-h:transform:bash {layout_bin} --move-preview left",
            f"alt-j:transform:bash {layout_bin} --move-preview down",
            f"alt-k:transform:bash {layout_bin} --move-preview up",
            f"alt-l:transform:bash {layout_bin} --move-preview right",
            f"alt-v:transform:bash {layout_bin} --move-preview hidden",
            "shift-up:preview-up", "shift-down:preview-down",
            "shift-scroll-up:preview-up", "shift-scroll-down:preview-down",
            "scroll-up:up", "scroll-down:down",
            "preview-scroll-up:preview-up", "preview-scroll-down:preview-down",
            "ctrl-/:toggle-preview",
            "alt-p:change-preview-window(down,50%,border-top|right,70%,border-left)",
        ])

    if bind_actions:
        # transform:COMMAND consumes the rest of its --bind argument. Separate
        # bindings also preserve commands containing commas or parentheses.
        fzf_cmd.extend(f"--bind={binding}" for binding in bind_actions)

    payload = "\0".join(choices) + "\0"

    fzf_env = os.environ.copy()
    for key in ("FZF_DEFAULT_OPTS", "FZF_DEFAULT_OPTS_FILE", "FZF_DEFAULT_COMMAND", *GIT_ENV_OVERRIDES):
        fzf_env.pop(key, None)
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

    return [
        line for line in proc.stdout.decode("utf-8", errors="surrogateescape").split("\0") if line
    ]


def commit_selected_index(files: list[str], message: str) -> None:
    """Commit selected staged entries, including deletions whose files still exist."""
    if not files:
        return
    for state in ("MERGE_HEAD", "CHERRY_PICK_HEAD", "REVERT_HEAD", "rebase-merge", "rebase-apply"):
        if (GIT_DIR / state).exists():
            raise RuntimeError(
                "Finish the active merge/rebase/cherry-pick before committing selected files."
            )
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
        run_git(
            "update-index", "-z", "--index-info",
            input_data=os.fsencode("".join(updates)),
            index_file=index, check=True,
        )
        run_git("commit", "-m", message, index_file=index, check=True)
    # Hooks may have rewritten selected entries. Update only those real index
    # entries; all unrelated staged changes and all disk files remain intact.
    try:
        run_git(
            "reset", "--quiet", "HEAD", "--pathspec-from-file=-", "--pathspec-file-nul",
            input_data=os.fsencode("\0".join(files) + "\0"),
            literal_pathspecs=True, check=True,
        )
    except subprocess.CalledProcessError as exc:
        raise RuntimeError(
            "Commit succeeded, but updating selected index entries failed; "
            "inspect git status before retrying."
        ) from exc


def commit_and_push(files: list[str] | None = None, local_only: bool = False) -> bool | None:
    """Commit the selected staged paths and optionally push the resulting commit."""
    _, staged_out, _ = run_git("diff", "--cached", "--name-only", "--no-renames", "-z", check=True)
    staged = {path for path in staged_out.split("\0") if path}
    commit_files = sorted(staged if files is None else staged.intersection(files))
    if not commit_files:
        console.print("[yellow]Nothing staged for the selected files.[/yellow]")
        return None

    console.print("\n[bold cyan]Commit Message (or type 'abort' to cancel)[/bold cyan]")
    while True:
        msg = ask()
        if not msg:
            console.print("[bold red]✖ Error: Commit message cannot be empty.[/bold red]")
            continue
        if msg.lower() in ("abort", "q"):
            console.print("[bold yellow]⚠ Aborted: Commit cancelled by user.[/bold yellow]")
            return None
        break

    try:
        commit_selected_index(commit_files, msg)
    except subprocess.CalledProcessError:
        console.print("[bold red]✖ Commit failed (Hooks/Formatting block).[/bold red]")
        return False

    if local_only:
        console.print("[bold green]✔[/bold green] Committed changes locally.")
        return None

    if ask_yesno("Push to the configured upstream?", default=True):
        return safe_push()
    return None


def upstream_target(branch: str) -> tuple[str, str] | None:
    """Return the actual configured remote and full destination branch ref."""
    _, out, _ = run_git(
        "for-each-ref", "--format=%(upstream:remotename)%00%(upstream:remoteref)",
        f"refs/heads/{branch}", check=True,
    )
    remote, _, ref = out.strip().partition("\0")
    return (remote, ref) if remote and ref else None


def safe_push(branch: str | None = None) -> bool:
    """Push to the configured upstream and check every recovery operation."""
    _, current, _ = run_git("branch", "--show-current", check=True)
    current = current.strip()
    if not current or (branch is not None and branch != current):
        console.print("[red]Push requires the active named branch.[/red]")
        return False
    if run_git("rev-parse", "--verify", "HEAD")[0] != 0:
        console.print("[red]No commits on current branch to push.[/red]")
        return False
    branch = current
    target = upstream_target(branch)
    if target is None:
        if not ask_yesno(f"Set upstream to origin/{branch} and push?", default=True):
            return False
        remote, ref = "origin", f"refs/heads/{branch}"
    else:
        remote, ref = target
    remote_branch_label = f"{remote}/{ref.removeprefix('refs/heads/')}"
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
        if not ask_yesno(f"Fast-forward to {remote_branch_label}?", default=False):
            return False
        run_git("merge", "--ff-only", fetched, capture=False, check=True)
        if target is None:
            run_git(*push_args, capture=False, check=True)
        return True
    console.print("1) Rebase local commits onto upstream\n2) Force push with lease\n3) Cancel")
    choice = ask("Choose [1/2/3] (default 3): ") or "3"
    if choice == "1":
        # Do not auto-abort: an existing or newly conflicted rebase needs inspection.
        if run_git("rebase", "--no-autostash", fetched, capture=False)[0]:
            console.print(
                "[yellow]Rebase stopped. Resolve it or run git rebase --abort before retrying.[/yellow]"
            )
            return False
        run_git(*push_args, capture=False, check=True)
    elif choice == "2" and ask_yesno(f"Overwrite {remote_branch_label} with local history?"):
        run_git(
            "push", "--set-upstream", f"--force-with-lease={ref}:{fetched}", "--", remote,
            f"refs/heads/{branch}:{ref}", capture=False, check=True,
        )
    else:
        return False
    console.print("[green]✔ Push successful.[/green]")
    return True


def restore_selected_files() -> bool | None:
    """Restore selected paths in the index and work tree to the last commit."""
    if run_git("rev-parse", "--verify", "HEAD")[0] != 0:
        console.print("[red]No HEAD exists to restore. No files changed.[/red]")
        return False
    entries = scoped_entries(tracked_only=True)
    if not entries:
        console.print("[green]No matching tracked changes to restore.[/green]")
        return None
    selected = select_changed_entries(
        entries,
        prompt="Restore Files to HEAD",
        notice="Enter: discard selected staged + unstaged edits; staged new files are removed. Esc: cancel.",
    )
    if not selected:
        return None
    paths = dict.fromkeys(
        name for path, old, status in selected
        for name in (path, old if "R" in status else None)
        if name is not None
    )
    payload = os.fsencode("\0".join(paths) + "\0")
    run_git(
        "restore", "--source=HEAD", "--staged", "--worktree", "--quiet",
        "--pathspec-from-file=-", "--pathspec-file-nul",
        input_data=payload, check=True, literal_pathspecs=True,
    )
    console.print(f"[green]✔ Restored {len(selected)} selected change(s) to HEAD.[/green]")
    return None


def discard_local_changes() -> bool | None:
    """Discards uncommitted changes (staged + unstaged), honoring manifest scope."""
    valid_paths = get_list_pathspecs()
    if valid_paths == []:
        console.print("[yellow]Empty manifest: nothing to discard.[/yellow]")
        return None
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
        return None

    scope_note = (
        "tracked files listed in your manifest"
        if valid_paths is not None
        else "ALL tracked files in the work tree"
    )
    console.print(Panel.fit(
        "[bold red]!!! DISCARD LOCAL CHANGES !!![/bold red]\n"
        f"This will permanently erase local changes of your choice.\n"
        f"Revert scope: [bold]{scope_note}[/bold].",
        border_style="red",
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
        return None

    # A staged deletion and an untracked replacement can share the same path.
    # Respect the separate approval for deleting that replacement.
    collisions = {
        tracked for tracked in tracked_scope for untracked in untracked_to_delete
        if tracked == untracked.rstrip("/")
        or tracked.startswith(untracked.rstrip("/") + "/")
        or untracked.startswith(tracked + "/")
    }
    if revert_tracked and collisions and not delete_untracked:
        console.print(
            "[yellow]Restore blocked: untracked replacements would be overwritten. No files changed.[/yellow]"
        )
        return False
    if revert_tracked and run_git("rev-parse", "--verify", "HEAD")[0] != 0:
        console.print("[red]No HEAD exists to restore. No files changed.[/red]")
        return False

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
            run_git(
                "restore", "--source=HEAD", "--staged", "--worktree", "--quiet",
                "--pathspec-from-file=-", "--pathspec-file-nul",
                input_data=payload, check=True, literal_pathspecs=True,
            )
            console.print("[green]✔ Tracked files restored.[/green]")
    except subprocess.CalledProcessError:
        console.print("[red]✖ Discard failed; inspect Git status before retrying.[/red]")
        return False
    return None


def reset_local_to_remote() -> bool | None:
    """Hard resets the local repository to match the remote branch tracking state."""
    console.print(Panel.fit(
        "[bold red]⚠ RESET LOCAL STATE TO MATCH GITHUB ⚠[/bold red]\n"
        "This will discard all local commits that haven't been pushed to GitHub\n"
        "AND erase all uncommitted edits on your disk, resetting everything to match the remote.",
        border_style="red",
    ))

    _, branch_out, _ = run_git("branch", "--show-current")
    branch_out = branch_out.strip()
    if not branch_out:
        console.print("[bold red]✖ Error: Detached HEAD state detected.[/bold red]")
        return False

    remote, ref = upstream_target(branch_out) or ("origin", f"refs/heads/{branch_out}")
    remote_label = f"{remote}/{ref.removeprefix('refs/heads/')}"

    if ask_yesno(f"Reset local state and overwrite all files to match {remote_label}?", default=False):
        try:
            console.print("[bold blue]Fetching latest state from GitHub...[/bold blue]")
            run_git("fetch", remote, ref, capture=False, check=True)

            console.print(f"[bold blue]Hard resetting to {remote_label}...[/bold blue]")
            run_git("reset", "--hard", "FETCH_HEAD", "--quiet", capture=False, check=True)
            console.print("[bold green]✔[/bold green] Local state successfully synced with GitHub remote.")
        except subprocess.CalledProcessError:
            console.print("[bold red]✖ Sync operation failed.[/bold red]")
            return False
    return None


def quick_step_back() -> bool | None:
    """Rolls back the repository by exactly 1 commit on both local and remote."""
    console.print(Panel.fit(
        "[bold red]⚠ DELETE LAST COMMIT FROM REMOTE ⚠[/bold red]\n"
        "This will hard-reset the local repository to HEAD~1 and force-push to origin,\n"
        "permanently deleting the last commit from both local and remote history.",
        border_style="red",
    ))

    _, branch_out, _ = run_git("branch", "--show-current", check=True)
    branch_out = branch_out.strip()
    if not branch_out:
        console.print("[bold red]✖ Error: Detached HEAD state detected. No files changed.[/bold red]")
        return False

    code, log_out, _ = run_git("log", "--format=%h", "-n", "2")
    if code != 0 or not log_out or len(log_out.splitlines()) < 2:
        console.print(
            "[bold red]✖ Error:[/bold red] Cannot step back. Must have at least two commits in history."
        )
        return False

    remote, ref = upstream_target(branch_out) or ("origin", f"refs/heads/{branch_out}")
    remote_label = f"{remote}/{ref.removeprefix('refs/heads/')}"

    if ask_yesno("Step back 1 commit on both local and remote?", default=False):
        try:
            run_git("reset", "--hard", "HEAD~1", "--quiet", capture=False, check=True)
            console.print("[bold green]✔[/bold green] Local repository reset to HEAD~1.")

            console.print(f"[bold blue]Force-pushing to {remote_label}...[/bold blue]")
            run_git(
                "push", "--force-with-lease", remote, f"refs/heads/{branch_out}:{ref}",
                capture=False, check=True,
            )
            console.print("[bold green]✔[/bold green] Step back 1 commit complete on remote.")
        except subprocess.CalledProcessError:
            console.print("[bold red]✖ Step back operation failed.[/bold red]")
            return False
    return None


def undo_local_commits_to_commit() -> bool | None:
    """Safe mixed reset to a selected past commit (uncommits files, keeping disk modifications)."""
    console.print(Panel.fit(
        "[bold yellow]⚠ UNDO LOCAL COMMITS TO A SPECIFIC COMMIT ⚠[/bold yellow]\n"
        "This will reset your local HEAD to a selected past commit,\n"
        "returning all files changed since that commit to your unstaged area.\n"
        "All edits on disk will be safely preserved.",
        border_style="yellow",
    ))

    _, log_out, _ = run_git("log", "--format=%h %s", "-n", "30")
    if not log_out:
        console.print("[bold red]✖ Error:[/bold red] No commit history found.")
        return False

    commits = log_out.splitlines()
    preview_cmd = get_commit_preview_cmd()

    target = fzf_select(
        commits,
        prompt="Select Target Commit",
        preview=preview_cmd,
        header=" ⚠ Commits NEWER than the selected commit will be undone (selected commit becomes new HEAD)",
    )
    if not target:
        return None

    commit_hash = target[0].split()[0]
    console.print(f"\n[bold yellow]Target Commit:[/bold yellow] {escape(target[0])}")

    if ask_yesno(f"Reset local HEAD to {commit_hash} and preserve edits?", default=False):
        try:
            run_git("reset", commit_hash, capture=False, check=True)
            console.print(
                f"[bold green]✔[/bold green] Local HEAD reset to {commit_hash}. Changes preserved in working tree."
            )
        except subprocess.CalledProcessError:
            console.print("[bold red]✖ Reset operation failed.[/bold red]")
            return False
    return None


def delete_local_commits_to_commit() -> bool | None:
    """Destructive hard reset to a selected past commit (uncommits files and wipes edits)."""
    console.print(Panel.fit(
        "[bold red]⚠ DELETE LOCAL COMMITS SINCE A SPECIFIC COMMIT ⚠[/bold red]\n"
        "This will permanently delete commits from your local history up to the selected past commit,\n"
        "AND erase all changes associated with those commits from your disk.",
        border_style="red",
    ))

    _, log_out, _ = run_git("log", "--format=%h %s", "-n", "30")
    if not log_out:
        console.print("[bold red]✖ Error:[/bold red] No commit history found.")
        return False

    commits = log_out.splitlines()
    preview_cmd = get_commit_preview_cmd()

    target = fzf_select(
        commits,
        prompt="Select Target Commit",
        preview=preview_cmd,
        header=" ⚠ Commits NEWER than the selected commit will be permanently deleted (selected commit becomes HEAD)",
    )
    if not target:
        return None

    commit_hash = target[0].split()[0]
    console.print(f"\n[bold yellow]Target Commit:[/bold yellow] {escape(target[0])}")

    if ask_yesno(f"Delete all local commits since {commit_hash} and discard all their edits?", default=False):
        try:
            run_git("reset", "--hard", commit_hash, "--quiet", capture=False, check=True)
            console.print(f"[bold green]✔[/bold green] Local state reset to {commit_hash}. Changes discarded.")
        except subprocess.CalledProcessError:
            console.print("[bold red]✖ Reset operation failed.[/bold red]")
            return False
    return None


def safe_revert_last_commit() -> bool | None:
    """Safe non-destructive revert that appends a new commit undoing the last commit."""
    console.print(Panel.fit(
        "[bold green]✔ UNDO LAST COMMIT SAFELY (Create Revert Commit) ✔[/bold green]\n"
        "This will create a new commit that undoes the changes of the last commit,\n"
        "preserving the commit history without rewriting it.",
        border_style="green",
    ))

    code, log_out, _ = run_git("log", "-n", "1")
    if code != 0 or not log_out:
        console.print("[bold red]✖ Error:[/bold red] No history found to revert.")
        return False

    if ask_yesno("Execute safe revert of the last commit?", default=True):
        try:
            is_merge = run_git("rev-parse", "--verify", "--quiet", "HEAD^2")[0] == 0
            revert_args = ["revert", "--no-edit", *(["-m", "1"] if is_merge else []), "HEAD"]
            run_git(*revert_args, capture=False, check=True)
            console.print("[bold green]✔[/bold green] Safe revert commit created locally.")

            if ask_yesno("Push the revert commit to remote?", default=True):
                return safe_push()
        except subprocess.CalledProcessError:
            console.print("[bold red]✖ Safe revert operation aborted or failed.[/bold red]")
            return False
    return None


def show_delta() -> None:
    """Pipes differential directly through Delta. Pure view — never mutates the index."""
    console.print("[bold blue]Executing Delta differential...[/bold blue]")
    base = ["HEAD"] if run_git("rev-parse", "--verify", "HEAD")[0] == 0 else ["--cached"]
    run_git("-c", "core.pager=delta", "diff", *base, capture=False, check=True)


def nuclear_revert() -> bool | None:
    """Absolute destructive timeline sync. Hard resets local tree and force-pushes."""
    console.print(Panel.fit(
        "[bold red]⚠ DELETE COMMITS FROM REMOTE ⚠[/bold red]\n"
        "This will permanently delete commits since the selected commit from local history\n"
        "AND force-push to overwrite the remote history on GitHub.",
        border_style="red",
    ))

    _, log_out, _ = run_git("log", "--format=%h %s", "-n", "30")
    if not log_out:
        return None

    commits = log_out.splitlines()
    preview_cmd = get_commit_preview_cmd()

    target = fzf_select(
        commits,
        prompt="Select Target Commit",
        preview=preview_cmd,
        header=" ⚠ Commits NEWER than the selected commit will be obliterated locally & on remote",
    )
    if not target:
        return None

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
                    console.print(
                        "[bold red]✖ Error: Detached HEAD state detected.[/bold red] Aborting force push."
                    )
                    return False

                remote, ref = upstream_target(branch_out) or ("origin", f"refs/heads/{branch_out}")
                run_git(
                    "push", "--force-with-lease", remote, f"refs/heads/{branch_out}:{ref}",
                    capture=False, check=True,
                )
                console.print("[bold green]✔[/bold green] Remote repository obliteration complete.")
        except subprocess.CalledProcessError:
            console.print("[bold red]✖ Force reset operation interrupted by error.[/bold red]")
            return False
    return None


def run_time_machine() -> bool | None:
    """Handoff execution to the highly-optimized Ephemeral Bash TUI."""
    if TIME_MACHINE_BIN.is_file() and os.access(TIME_MACHINE_BIN, os.X_OK):
        console.print("[bold blue]Engaging ZRAM Ephemeral Time Machine...[/bold blue]")
        subprocess.run([str(TIME_MACHINE_BIN)], check=True)
        return None
    console.print(
        f"[bold red]✖ Error:[/bold red] Time machine binary not found or not executable at "
        f"{escape(str(TIME_MACHINE_BIN))}"
    )
    return False


def checkout_pr() -> bool | None:
    """Fetches a GitHub Pull Request by URL or number and checks out a local branch without merging."""
    console.print(Panel.fit(
        "[bold cyan]󰏖 CHECKOUT GITHUB PULL REQUEST[/bold cyan]\n"
        "Fetch a Pull Request locally for editing/testing without merging into main.",
        border_style="cyan",
    ))
    console.print(
        "[bold cyan]Enter GitHub PR URL or PR Number "
        "(e.g. 268 or https://github.com/owner/repo/pull/268)[/bold cyan]"
    )
    user_input = ask()
    if not user_input or user_input.lower() in ("q", "abort", "exit"):
        console.print("[bold yellow]⚠ Aborted PR checkout.[/bold yellow]")
        return None

    match = re.fullmatch(r"(?:https://github\.com/[^/]+/[^/]+/pull/)?([1-9]\d*)/?", user_input)
    if not match:
        console.print(
            f"[bold red]✖ Error: Could not parse a valid PR number from '{escape(user_input)}'.[/bold red]"
        )
        return False

    pr_num = match.group(1)
    branch_name = f"pr/{pr_num}"

    code, _, _ = run_git("show-ref", "--verify", "--quiet", f"refs/heads/{branch_name}")
    if code == 0:
        console.print(
            f"[bold yellow]⚠[/bold yellow] Local branch '{branch_name}' already exists — "
            "switching to it without fetching."
        )
        try:
            run_git("switch", branch_name, capture=False, check=True)
            console.print(f"[bold green]✔[/bold green] Switched to existing branch '{branch_name}'.")
        except subprocess.CalledProcessError:
            console.print(f"[bold red]✖ Failed to switch to '{branch_name}'.[/bold red]")
            return False
        return None

    console.print(f"[bold blue]Fetching PR #{pr_num} into branch '{branch_name}'...[/bold blue]")
    try:
        run_git("fetch", "origin", f"pull/{pr_num}/head:{branch_name}", capture=False, check=True)
        console.print(f"[bold blue]Switching to branch '{branch_name}'...[/bold blue]")
        run_git("switch", branch_name, capture=False, check=True)
        console.print(Panel.fit(
            f"[bold green]✔ Checked out PR #{pr_num} on branch '{branch_name}'![/bold green]\n\n"
            f"You can now edit and test your files locally.\n"
            f"When done, use option 7 ([bold yellow]Branch Management[/bold yellow]) to return to 'main'.",
            border_style="green",
        ))
    except subprocess.CalledProcessError:
        console.print(
            f"[bold red]✖ Failed to checkout PR #{pr_num}. Make sure the PR exists on GitHub remote.[/bold red]"
        )
        return False
    return None


def create_branch() -> bool | None:
    """Creates a new local branch and optionally checks it out."""
    console.print("\n[bold cyan]Enter new branch name (or 'abort' to cancel):[/bold cyan]")
    name = ask()
    if not name or name.lower() in ("abort", "q"):
        console.print("[bold yellow]⚠ Branch creation cancelled.[/bold yellow]")
        return None

    clean_name = name.replace(" ", "-")

    if clean_name.startswith("-") or run_git("check-ref-format", "--branch", clean_name)[0]:
        console.print("[red]Invalid branch name.[/red]")
        return None
    checkout = ask_yesno(f"Switch to branch '{clean_name}' immediately?", default=True)

    try:
        if checkout:
            run_git("switch", "-c", clean_name, capture=False, check=True)
            console.print(
                f"[bold green]✔ Branch '{clean_name}' created and switched to successfully.[/bold green]"
            )
        else:
            run_git("branch", clean_name, capture=False, check=True)
            console.print(f"[bold green]✔ Branch '{clean_name}' created successfully.[/bold green]")
    except subprocess.CalledProcessError:
        console.print(f"[bold red]✖ Failed to create branch '{clean_name}'.[/bold red]")
        return False
    return None


def switch_branch() -> bool | None:
    """Interactively lists and switches local branches using FZF with commit preview."""
    code, branches_out, _ = run_git("branch", "--format=%(refname:short)")
    if code != 0 or not branches_out.strip():
        console.print("[bold red]✖ Error: Failed to list local branches.[/bold red]")
        return False

    branches = [b.strip() for b in branches_out.splitlines() if b.strip()]
    if not branches:
        console.print("[bold yellow]⚠ No local branches found.[/bold yellow]")
        return None

    _, current_branch, _ = run_git("branch", "--show-current")
    current_branch = current_branch.strip()

    # Place non-active branches first so the fzf cursor starts on a switchable branch.
    branches.sort(key=lambda b: b == current_branch)

    console.print(f"[bold cyan]Current branch:[/bold cyan] [bold green]{escape(current_branch)}[/bold green]")
    preview_cmd = "git --no-advice log -n 10 --oneline --color=always {1}"
    selected = fzf_select(branches, prompt="Select Branch to Switch To", preview=preview_cmd)
    if not selected:
        return None

    target_branch = selected[0]
    if target_branch == current_branch:
        console.print(f"[bold yellow]⚠ Already on branch '{escape(target_branch)}'.[/bold yellow]")
        return None

    try:
        run_git("switch", target_branch, capture=False, check=True)
        console.print(f"[bold green]✔ Successfully switched to branch '{escape(target_branch)}'.[/bold green]")
    except subprocess.CalledProcessError:
        console.print(f"[bold red]✖ Failed to switch to branch '{escape(target_branch)}'.[/bold red]")
        return False
    return None


def merge_branch() -> bool | None:
    """Merges a selected branch into the current branch."""
    _, current_branch, _ = run_git("branch", "--show-current")
    current_branch = current_branch.strip()
    if not current_branch:
        console.print("[bold red]✖ Error: Detached HEAD state. Cannot merge.[/bold red]")
        return False

    code, branches_out, _ = run_git("branch", "--format=%(refname:short)")
    if code != 0 or not branches_out.strip():
        console.print("[bold red]✖ Error: Failed to list local branches.[/bold red]")
        return False

    other_branches = [
        b.strip() for b in branches_out.splitlines() if b.strip() and b.strip() != current_branch
    ]
    if not other_branches:
        console.print("[bold yellow]⚠ No other local branches available to merge.[/bold yellow]")
        return None

    preview_cmd = "git --no-advice log -n 10 --oneline --color=always {1}"
    console.print(
        f"[bold cyan]Merging into active branch:[/bold cyan] [bold green]{escape(current_branch)}[/bold green]"
    )
    selected = fzf_select(other_branches, prompt="Select Branch to Merge IN", preview=preview_cmd)
    if not selected:
        return None

    source_branch = selected[0]
    if not ask_yesno(f"Merge branch '{source_branch}' into '{current_branch}'?", default=True):
        console.print("[bold yellow]⚠ Merge operation cancelled.[/bold yellow]")
        return None

    try:
        run_git("merge", source_branch, capture=False, check=True)
        console.print(
            f"[bold green]✔ Successfully merged branch '{escape(source_branch)}' "
            f"into '{escape(current_branch)}'.[/bold green]"
        )
    except subprocess.CalledProcessError:
        console.print(
            "[bold red]✖ Merge failed or encountered conflicts. Resolve conflicts and commit.[/bold red]"
        )
        return False
    return None


def push_branch_to_remote() -> bool | None:
    """Pushes current or selected branch to remote origin and sets upstream."""
    _, current_branch, _ = run_git("branch", "--show-current")
    current_branch = current_branch.strip()
    if not current_branch:
        console.print("[bold red]✖ Error: Detached HEAD state. Cannot push branch.[/bold red]")
        return False

    if ask_yesno(f"Push current branch '{current_branch}' to origin remote (set-upstream)?", default=True):
        return safe_push(current_branch)
    return None


def delete_local_branch() -> bool | None:
    """Deletes a local branch."""
    _, current_branch, _ = run_git("branch", "--show-current")
    current_branch = current_branch.strip()

    code, branches_out, _ = run_git("branch", "--format=%(refname:short)")
    if code != 0 or not branches_out.strip():
        console.print("[bold red]✖ Error: Failed to list local branches.[/bold red]")
        return False

    other_branches = [
        b.strip() for b in branches_out.splitlines() if b.strip() and b.strip() != current_branch
    ]
    if not other_branches:
        console.print("[bold yellow]⚠ No other local branches to delete.[/bold yellow]")
        return None

    preview_cmd = "git --no-advice log -n 10 --oneline --color=always {1}"
    selected = fzf_select(other_branches, prompt="Select Local Branch to DELETE", preview=preview_cmd)
    if not selected:
        return None

    target_branch = selected[0]
    if ask_yesno(f"Force delete local branch '{target_branch}'?", default=False):
        try:
            run_git("branch", "-D", target_branch, capture=False, check=True)
            console.print(f"[bold green]✔ Local branch '{target_branch}' deleted successfully.[/bold green]")
        except subprocess.CalledProcessError:
            console.print(f"[bold red]✖ Failed to delete local branch '{target_branch}'.[/bold red]")
            return False
    return None


def delete_remote_branch() -> bool | None:
    """Deletes a remote branch on GitHub origin."""
    code, refs_out, _ = run_git("branch", "-r", "--format=%(refname:short)")
    if code != 0 or not refs_out.strip():
        console.print("[bold red]✖ Error: Failed to list remote branches.[/bold red]")
        return False

    remote_branches = []
    for line in refs_out.splitlines():
        b = line.strip()
        if b.startswith("origin/") and not b.endswith("/HEAD"):
            remote_branches.append(b.removeprefix("origin/"))

    if not remote_branches:
        console.print("[bold yellow]⚠ No remote branches found.[/bold yellow]")
        return None

    selected = fzf_select(remote_branches, prompt="Select Remote Branch on GitHub to DELETE")
    if not selected:
        return None

    target_branch = selected[0]
    console.print(Panel.fit(
        f"[bold red]⚠ DELETE REMOTE BRANCH ⚠[/bold red]\n"
        f"This will permanently delete 'origin/{escape(target_branch)}' from GitHub remote repository!",
        border_style="red",
    ))
    if ask_yesno(f"Are you sure you want to delete 'origin/{target_branch}' on GitHub?", default=False):
        try:
            run_git("push", "origin", "--delete", target_branch, capture=False, check=True)
            console.print(
                f"[bold green]✔ Remote branch 'origin/{target_branch}' deleted successfully.[/bold green]"
            )
        except subprocess.CalledProcessError:
            console.print(
                f"[bold red]✖ Failed to delete remote branch 'origin/{target_branch}'.[/bold red]"
            )
            return False
    return None


def list_all_branches() -> None:
    """Displays detailed list of all local and remote branches."""
    console.print("\n[bold cyan]All Local & Remote Branches:[/bold cyan]")
    run_git("-c", "color.ui=always", "branch", "-a", "-v", capture=False, check=True)


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
            box=box.ROUNDED,
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


def push_existing() -> bool | None:
    """Option 4: pushes existing local commits on the current branch safely."""
    return safe_push()


# --- 6. STASH MANAGEMENT ---
def get_stash_list() -> list[str]:
    """Retrieves list of stashes formatted for display and selection."""
    _, stash_out, _ = run_git("stash", "list", check=True)
    if not stash_out.strip():
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


def create_stash() -> bool | None:
    """Creates a stash with proper naming scheme: dusky-stash-YYYYMMDD-HHMMSS: description."""
    console.print("\n[bold cyan]Enter Stash Description / Label (or 'abort' to cancel):[/bold cyan]")
    desc = ask()
    if not desc or desc.lower() in ("abort", "q"):
        console.print("[bold yellow]⚠ Stash creation cancelled.[/bold yellow]")
        return None

    timestamp = datetime.now().astimezone().strftime("%Y%m%d-%H%M%S")
    stash_msg = f"dusky-stash-{timestamp}: {desc}"

    include_untracked = ask_yesno("Include untracked files in stash?", default=False)

    stash_args = ["stash", "push", "-m", stash_msg]
    if include_untracked:
        stash_args.append("-u")

    input_payload: bytes | None = None
    use_literal = False

    # Scope stash operations to the manifest when present so `git stash push -u`
    # never cleans untracked files outside ~/.git_dusky_list (Design Law #2).
    if DOTFILES_LIST.is_file():
        entries = scoped_entries(tracked_only=not include_untracked)
        if not entries:
            console.print(
                "[bold yellow]⚠ Nothing to stash — working tree had no matching local changes.[/bold yellow]"
            )
            return None
        scoped_paths = sorted({
            name for path, old, _ in entries
            for name in (path, old) if name is not None
        })
        stash_args.extend(["--pathspec-from-file=-", "--pathspec-file-nul"])
        input_payload = os.fsencode("\0".join(scoped_paths) + "\0")
        use_literal = True
    elif include_untracked and WORK_TREE == HOME:
        console.print(
            "[bold red]✖ Refusing unscoped untracked stash in $HOME without ~/.git_dusky_list.[/bold red]"
        )
        return False

    before = len(get_stash_list())
    try:
        run_git(
            *stash_args,
            capture=False,
            check=True,
            input_data=input_payload,
            literal_pathspecs=use_literal,
        )
    except subprocess.CalledProcessError:
        console.print("[bold red]✖ Failed to create stash.[/bold red]")
        return False

    if len(get_stash_list()) > before:
        console.print(f"[bold green]✔ Stash created successfully:[/bold green] [dim]{escape(stash_msg)}[/dim]")
    else:
        console.print(
            "[bold yellow]⚠ Nothing to stash — working tree had no matching local changes.[/bold yellow]"
        )
    return None


def pop_or_apply_stash(action: str = "pop") -> bool | None:
    """Pops or applies a stash selected via FZF with diff preview."""
    stashes = get_stash_list()
    if not stashes:
        console.print("[bold yellow]⚠ No stashes found in repository.[/bold yellow]")
        return None

    preview_cmd = get_stash_preview_cmd()
    prompt_text = f"Select Stash to {action.upper()}"

    selected = fzf_select(stashes, prompt=prompt_text, preview=preview_cmd)
    if not selected:
        return None

    stash_ref = selected[0].split(":")[0].strip()

    try:
        run_git("stash", action, stash_ref, capture=False, check=True)
        msg = (
            f"Successfully popped {stash_ref}."
            if action == "pop"
            else f"Successfully applied {stash_ref}."
        )
        console.print(f"[bold green]✔ {msg}[/bold green]")
    except subprocess.CalledProcessError:
        _, conflict_out, _ = run_git("status", "--porcelain=v1")
        if any(e[:2] in CONFLICT_STATUSES for e in conflict_out.splitlines()):
            console.print(
                f"[bold red]✖ Conflict while applying {stash_ref} — stash kept intact. "
                "Resolve, then drop manually.[/bold red]"
            )
        else:
            console.print(f"[bold red]✖ Failed to {action} {stash_ref}.[/bold red]")
        return False
    return None


def drop_stash() -> bool | None:
    """Drops/deletes a selected stash via FZF."""
    stashes = get_stash_list()
    if not stashes:
        console.print("[bold yellow]⚠ No stashes found in repository.[/bold yellow]")
        return None

    preview_cmd = get_stash_preview_cmd()
    selected = fzf_select(stashes, prompt="Select Stash to DROP/DELETE", preview=preview_cmd)
    if not selected:
        return None

    stash_ref = selected[0].split(":")[0].strip()
    stash_desc = selected[0]

    if ask_yesno(f"Permanently delete {stash_desc}?", default=False):
        try:
            run_git("stash", "drop", stash_ref, capture=False, check=True)
            console.print(f"[bold green]✔ Successfully dropped {stash_ref}.[/bold green]")
        except subprocess.CalledProcessError:
            console.print(f"[bold red]✖ Failed to drop {stash_ref}.[/bold red]")
            return False
    return None


def clear_stashes() -> bool | None:
    """Clears all stashes in repository."""
    stashes = get_stash_list()
    if not stashes:
        console.print("[bold yellow]⚠ No stashes found in repository.[/bold yellow]")
        return None

    console.print(Panel.fit(
        f"[bold red]⚠ CLEAR ALL STASHES ⚠[/bold red]\n"
        f"This will permanently delete all {len(stashes)} stash entry/entries!",
        border_style="red",
    ))
    if ask_yesno("Are you absolutely sure you want to clear ALL stashes?", default=False):
        try:
            run_git("stash", "clear", capture=False, check=True)
            console.print("[bold green]✔ All stashes cleared successfully.[/bold green]")
        except subprocess.CalledProcessError:
            console.print("[bold red]✖ Failed to clear stashes.[/bold red]")
            return False
    return None


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
            box=box.ROUNDED,
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
    handler: Callable[[], bool | None]


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
    Action("10", "Restore Selected File(s) to HEAD (Staged & Unstaged)",                     2, True,  restore_selected_files),
    Action("11", "Undo Last Commit Safely (Creates Revert Commit on Local & Remote)",        3, False, safe_revert_last_commit),
    Action("12", "Undo Local Commits to a Specific Commit (Safe - keeps edits on disk)",     3, False, undo_local_commits_to_commit),
    Action("13", "Reset Local Branch State to Match GitHub",                                 3, True,  reset_local_to_remote),
    Action("14", "Delete Local Commits since a Specific Commit",                             4, True,  delete_local_commits_to_commit),
    Action("15", "Delete Last Commit from Remote (Force Push HEAD~1)",                       4, True,  quick_step_back),
    Action("16", "Delete Commits since a Specific Commit from Remote (Nuclear Force Push)",  4, True,  nuclear_revert),
    Action("17", "Engage Ephemeral Time Machine (TUI)",                                      4, False, run_time_machine),
)

ACTION_MAP: dict[str, Action] = {a.key: a for a in ACTIONS}
VALID_KEYS: frozenset[str] = frozenset(ACTION_MAP)
IMMEDIATE_RETURN_HANDLERS: frozenset[Callable[[], bool | None]] = frozenset({
    manage_branches,
    manage_stashes,
    run_time_machine,
})


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


def _active_sequencer_badge() -> str:
    """Returns a warning badge if a merge/rebase/cherry-pick/revert is active."""
    for marker, label in (
        ("MERGE_HEAD", "MERGE IN PROGRESS"),
        ("rebase-merge", "REBASE IN PROGRESS"),
        ("rebase-apply", "REBASE IN PROGRESS"),
        ("CHERRY_PICK_HEAD", "CHERRY-PICK IN PROGRESS"),
        ("REVERT_HEAD", "REVERT IN PROGRESS"),
    ):
        if (GIT_DIR / marker).exists():
            return f"  │  [bold {COLORS['error']}]⚠ {label}[/bold {COLORS['error']}]"
    return ""


def render_dashboard() -> None:
    """Renders the categorized dashboard panels straight from the ACTION registry."""
    _, current_branch_raw, _ = run_git("branch", "--show-current")
    active_branch = current_branch_raw.strip() or "Detached HEAD"

    c_acc = COLORS["accent"]
    c_suc = COLORS["success"]
    seq_badge = _active_sequencer_badge()

    console.print(
        f"[bold {c_acc}]󰏖 Dusky Dotfiles Manager[/bold {c_acc}]  │  "
        f"[bold {c_acc}]Active Branch:[/bold {c_acc}] "
        f"[bold {c_suc}]{escape(active_branch)}[/bold {c_suc}]{seq_badge}\n"
    )

    for idx, (title, colorkey) in enumerate(CATEGORIES):
        c = COLORS.get(colorkey, "white")
        rows = [
            f"[bold {c}]{a.key:>2}[/bold {c}] │ "
            + (
                f"[bold {COLORS['error']}]{escape(a.label)}[/bold {COLORS['error']}]"
                if a.destructive
                else escape(a.label)
            )
            for a in ACTIONS
            if a.category == idx
        ]
        if idx == len(CATEGORIES) - 1:
            c_err = COLORS["error"]
            rows.append(f"[bold {c_err}] q[/bold {c_err}] │ Quit Dashboard")
        console.print(Panel.fit(
            "\n".join(rows),
            title=f"[bold {c}]  {title}[/bold {c}]",
            border_style=c,
            title_align="left",
            box=box.ROUNDED,
        ))


# --- 8. MAIN ROUTING ENGINE ---
def dispatch(choice: str) -> bool:
    """Executes a registry action by key."""
    try:
        handler = ACTION_MAP[choice].handler
        if handler in (run_time_machine, show_delta):
            return handler() is not False
        # Share the same advisory lock as Time Machine so our own tools cannot
        # stage/reset the work tree while the other tool is switching snapshots.
        lock_dir = GIT_DIR / "dusky-time-machine"
        lock_dir.mkdir(mode=0o700, exist_ok=True)
        with (lock_dir / "worktree.lock").open("a") as lock:
            try:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                console.print("[yellow]Another Dusky operation is using this repository.[/yellow]")
                return False
            return handler() is not False
    except (subprocess.CalledProcessError, OSError, ValueError, RuntimeError) as exc:
        console.print(f"[red]✖ Operation stopped: {escape(str(exc))}[/red]")
        return False


def main() -> Never:
    if len(sys.argv) > 1:
        if len(sys.argv) != 2:
            console.print("[red]✖ Expected one action or help/version option.[/red]")
            sys.exit(2)
        choice = sys.argv[1].strip()
        if choice in ("-h", "--help", "help", "h"):
            print_help()
            sys.exit(0)
        if choice in ("-V", "--version", "version"):
            console.print(f"dusky v{DUSKY_VERSION}")
            sys.exit(0)
        if choice in VALID_KEYS:
            check_dependencies()
            sys.exit(0 if dispatch(choice) else 1)
        console.print(f"[bold red]✖ Invalid choice argument '{escape(choice)}'.[/bold red]")
        print_help()
        sys.exit(1)

    check_dependencies()
    while True:
        console.clear()
        render_dashboard()

        console.print(
            f"\n[bold {COLORS['accent']}]Awaiting Directive [1-{len(ACTIONS)}/h/q][/bold {COLORS['accent']}]"
        )
        choice = ask().lower()
        while choice not in VALID_KEYS and choice not in ("q", "h", "help", "?"):
            if not choice:
                console.print(
                    "[bold yellow]⚠ No default action — enter a number explicitly (safety first).[/bold yellow]"
                )
            else:
                console.print("[bold red]✖ Invalid choice. Please select a valid key.[/bold red]")
            choice = ask().lower()

        if choice == "q":
            raise SystemExit(0)

        if choice in ("h", "help", "?"):
            print_help()
            console.print("\n[dim]Press [Enter] to return to dashboard...[/dim]")
            ask()
            continue

        dispatch(choice)

        if ACTION_MAP[choice].handler not in IMMEDIATE_RETURN_HANDLERS:
            console.print("\n[dim]Press [Enter] to return to dashboard...[/dim]")
            ask()


if __name__ == "__main__":
    try:
        if len(sys.argv) > 1 and sys.argv[1].startswith("--"):
            _act = sys.argv[1]
            helper_limits = {
                "--resize-preview": 3, "--move-preview": 3,
                "--toggle-vim": 3, "--vim-init": 3, "--key-escape": 2,
            }
            if _act in helper_limits and len(sys.argv) > helper_limits[_act]:
                raise ValueError(f"Too many arguments for {_act}")
            if _act in {"--resize-preview", "--move-preview"}:
                default = "left" if _act == "--resize-preview" else "right"
                handle_fzf_layout(_act, sys.argv[2] if len(sys.argv) > 2 else default)
                sys.exit(0)
            elif _act == "--toggle-vim":
                handle_fzf_toggle_vim(sys.argv[2] if len(sys.argv) > 2 else "Select")
                sys.exit(0)
            elif _act == "--vim-init":
                handle_fzf_vim_init(sys.argv[2] if len(sys.argv) > 2 else "")
                sys.exit(0)
            elif _act == "--key-escape":
                handle_fzf_escape()
                sys.exit(0)
            elif _act in ("--diff-preview", "--diff-preview-json"):
                if len(sys.argv) < 4:
                    raise ValueError(f"Usage: {_act} TARGET CURRENT [SELECTED ...]")
                target = sys.argv[2] if len(sys.argv) > 2 else ""
                cur = sys.argv[3] if len(sys.argv) > 3 else ""
                selected = sys.argv[4:] if len(sys.argv) > 4 else []
                if _act == "--diff-preview-json":
                    cur = json.loads(cur) if cur else ""
                    selected = [json.loads(path) for path in selected]
                handle_diff_preview(target, cur, selected)
                sys.exit(0)
        main()
    except (subprocess.CalledProcessError, OSError, ValueError, RuntimeError) as exc:
        from rich.markup import escape as _err_escape
        _print_err(f"[red]✖ Operation stopped: {_err_escape(str(exc))}[/red]")
        sys.exit(1)
    except KeyboardInterrupt:
        _print_err("\n[bold yellow]⚠ Execution Terminated.[/bold yellow]")
        sys.exit(0)
