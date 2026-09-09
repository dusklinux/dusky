#!/usr/bin/env python3
"""
Dusky Scripts Finder

Required:
    Python, fzf, Bash

Optional:
    bat        Syntax-highlighted source previews
    wl-copy    Wayland clipboard
    xclip      X11 clipboard
    nvim       Edit action
    yazi       Reveal action

Theme:
    ~/.config/matugen/generated/dusky_tui.json

History:
    ~/.config/dusky/settings/scripts_fzf/state.json

Browser:
    Enter          Open action menu
    Ctrl-P         Toggle preview
    Alt-I          File information + source
    Alt-S          Source only
    Page Up/Down   Scroll preview
    Shift-Up/Down  Scroll preview one line
    Esc            Cancel

Actions:
    Enter          Perform selected action
    Alt-R          Run and copy path
    Alt-T          Put run command in prompt
    Alt-C          Copy path
    Alt-B          Copy path and put command in prompt
    Alt-E          Edit in Neovim
    Alt-Y          Reveal in Yazi
    Esc            Cancel
"""

from __future__ import annotations

import argparse
from datetime import datetime
import fcntl
import json
import os
from pathlib import Path
import re
import shlex
import shutil
import stat
import subprocess
import sys
import tempfile


HOME = Path.home()
DEFAULT_ROOT = HOME / "user_scripts"
THEME_FILE = HOME / ".config/matugen/generated/dusky_tui.json"

STATE_DIR = HOME / ".config/dusky/settings/scripts_fzf"
STATE_FILE = STATE_DIR / "state.json"
LEGACY_STATE = STATE_DIR / "state"
LOCK_FILE = STATE_DIR / "state.lock"

THEME_KEYS = ("bg", "fg", "accent", "error", "warning", "success", "muted")

ACTION_KEYS = {
    "alt-r": 1,
    "alt-t": 2,
    "alt-c": 3,
    "alt-b": 4,
    "alt-e": 5,
    "alt-y": 6,
}

PREVIEW_LINES = 500
RESET = "\033[0m"
BOLD = "\033[1m"


class FinderError(Exception):
    """An error suitable for display without a traceback."""


def warn(message: str) -> None:
    print(f"Warning: {message}", file=sys.stderr)


def require(name: str) -> str:
    executable = shutil.which(name)
    if executable is None:
        raise FinderError(f"Required command not found: {name}")
    return executable


def visible(text: str) -> str:
    """Escape control characters for display, without changing real paths."""
    return "".join(
        character if character.isprintable() else ascii(character)[1:-1]
        for character in text
    )


def ansi(color: str) -> str:
    red, green, blue = (
        int(color[index:index + 2], 16)
        for index in (1, 3, 5)
    )
    return f"\033[38;2;{red};{green};{blue}m"


def load_theme() -> dict[str, str]:
    try:
        data = json.loads(THEME_FILE.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, ValueError) as exc:
        raise FinderError(
            f"Cannot load theme {THEME_FILE}: {exc}"
        ) from exc

    if not isinstance(data, dict):
        raise FinderError(f"{THEME_FILE}: expected a JSON object")

    for key in THEME_KEYS:
        value = data.get(key)
        if not isinstance(value, str) or not re.fullmatch(
            r"#[0-9a-fA-F]{6}", value
        ):
            raise FinderError(
                f"{THEME_FILE}: {key!r} must contain a #RRGGBB color"
            )

    return {key: data[key] for key in THEME_KEYS}


def fzf_colors(theme: dict[str, str]) -> str:
    roles = {
        "bg": "bg",
        "bg+": "muted",
        "fg": "fg",
        "fg+": "fg",
        "hl": "accent",
        "hl+": "accent",
        "header": "accent",
        "info": "warning",
        "prompt": "accent",
        "pointer": "success",
        "marker": "success",
        "spinner": "warning",
        "border": "muted",
        "label": "accent",
        "gutter": "bg",
        "separator": "muted",
        "scrollbar": "muted",
        "input-border": "muted",
        "list-border": "muted",
        "header-border": "muted",
        "preview-bg": "bg",
        "preview-fg": "fg",
        "preview-border": "muted",
        "preview-label": "accent",
        "preview-scrollbar": "muted",
    }
    return ",".join(
        f"{role}:{theme[key]}" for role, key in roles.items()
    )


def read_weights() -> dict[str, int]:
    if STATE_FILE.exists():
        try:
            data = json.loads(STATE_FILE.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, ValueError) as exc:
            raise FinderError(
                f"Cannot read history {STATE_FILE}: {exc}"
            ) from exc

        if not isinstance(data, dict):
            raise FinderError(f"{STATE_FILE}: expected a JSON object")

        for path, weight in data.items():
            if (
                not isinstance(path, str)
                or not os.path.isabs(path)
                or "\0" in path
                or type(weight) is not int
                or weight < 0
            ):
                raise FinderError(
                    f"{STATE_FILE}: invalid history entry"
                )

        return data

    weights: dict[str, int] = {}

    if LEGACY_STATE.exists():
        try:
            text = LEGACY_STATE.read_text(
                encoding="utf-8",
                errors="surrogateescape",
            )
        except OSError as exc:
            raise FinderError(
                f"Cannot read legacy history {LEGACY_STATE}: {exc}"
            ) from exc

        for line in text.splitlines():
            weight, separator, path = line.partition("\t")
            if (
                separator
                and re.fullmatch(r"[0-9]{1,18}", weight)
                and os.path.isabs(path)
                and "\0" not in path
            ):
                weights[path] = int(weight)

    return weights


def record_use(path: str) -> None:
    """Merge a history increment under a lock, then replace atomically."""
    temporary: str | None = None

    try:
        STATE_DIR.mkdir(parents=True, exist_ok=True)

        with LOCK_FILE.open("a") as lock:
            fcntl.flock(lock.fileno(), fcntl.LOCK_EX)

            # Reread after locking to preserve other terminals' updates.
            weights = read_weights()
            weights = {
                old_path: weight
                for old_path, weight in weights.items()
                if os.path.isfile(old_path)
            }
            weights[path] = weights.get(path, 0) + 1

            with tempfile.NamedTemporaryFile(
                mode="w",
                encoding="utf-8",
                dir=STATE_DIR,
                prefix=".state-",
                delete=False,
            ) as output:
                temporary = output.name
                json.dump(
                    weights,
                    output,
                    ensure_ascii=True,
                    sort_keys=True,
                )
                output.write("\n")
                output.flush()
                os.fsync(output.fileno())

            os.replace(temporary, STATE_FILE)
            temporary = None

    except (FinderError, OSError, ValueError) as exc:
        warn(f"Could not update usage history: {exc}")

    finally:
        if temporary is not None:
            try:
                os.unlink(temporary)
            except OSError:
                pass


def discover(root: Path) -> list[str]:
    if not root.is_dir():
        raise FinderError(f"Scripts directory does not exist: {root}")

    helper_path = os.path.realpath(__file__)
    files: list[str] = []

    def walk_error(error: OSError) -> None:
        raise error

    try:
        # Hidden directories are included.
        # Directory symlinks are not traversed, avoiding cycles.
        # Symlinks to regular script files are allowed.
        for directory, _, names in os.walk(
            root,
            followlinks=False,
            onerror=walk_error,
        ):
            for name in names:
                if Path(name).suffix not in {".sh", ".py"}:
                    continue

                path = os.path.join(directory, name)

                if not os.path.isfile(path):
                    continue
                if os.path.realpath(path) == helper_path:
                    continue

                files.append(path)

    except OSError as exc:
        raise FinderError(f"Cannot scan {root}: {exc}") from exc

    if not files:
        raise FinderError(f"No .sh or .py files found in {root}")

    return files


def home_path(path: str) -> str:
    """Return an unquoted home-relative path for display."""
    relative = os.path.relpath(path, HOME)

    if relative == ".":
        return "~"
    if relative != ".." and not relative.startswith("../"):
        return "~/" + relative

    return path


def quoted_path(path: str) -> str:
    """Return a shell-quoted path suitable for the Zsh prompt."""
    relative = os.path.relpath(path, HOME)

    if relative == ".":
        return "~"
    if relative != ".." and not relative.startswith("../"):
        # Keep ~/ unquoted so the shell expands it.
        return "~/" + shlex.quote(relative)

    return shlex.quote(path)


def choose(
    rows: list[str],
    options: list[str],
    theme: dict[str, str],
    *,
    rank_matches: bool = False,
    extra_environment: dict[str, str] | None = None,
    shortcuts: dict[str, int] | None = None,
) -> int | None:
    environment = os.environ.copy()
    environment["FZF_DEFAULT_OPTS"] = ""
    environment["FZF_DEFAULT_OPTS_FILE"] = "/dev/null"

    if extra_environment:
        environment.update(extra_environment)

    command = [
        require("fzf"),
        "--read0",
        "--print0",
        "--ansi",
        "--no-multi",
        "--sort" if rank_matches else "--no-sort",
        "--exact",
        "--cycle",
        "--layout=reverse",
        "--border=rounded",
        "--delimiter=\t",
        "--with-nth=2..",
        "--prompt=❯ ",
        "--pointer=❯ ",
        "--color=" + fzf_colors(theme),
        *options,
    ]

    if shortcuts:
        command.append("--expect=" + ",".join(shortcuts))

    payload = ("\0".join(rows) + "\0").encode("utf-8")

    result = subprocess.run(
        command,
        input=payload,
        stdout=subprocess.PIPE,
        env=environment,
        check=False,
    )

    if result.returncode in (1, 130, -2):
        return None
    if result.returncode != 0:
        raise FinderError(f"fzf exited with status {result.returncode}")

    if not result.stdout.endswith(b"\0"):
        raise FinderError("fzf returned an invalid selection")

    records = result.stdout[:-1].split(b"\0")

    if shortcuts:
        # With --print0, the expected-key record is NUL-delimited too.
        key = records.pop(0).decode("ascii", errors="strict")

        if key:
            if key not in shortcuts:
                raise FinderError("fzf returned an unknown shortcut")
            return shortcuts[key]

    if len(records) != 1:
        raise FinderError("fzf returned an invalid selection")

    identifier, separator, _ = records[0].partition(b"\t")

    if not separator or not identifier.isdigit():
        raise FinderError("fzf returned an invalid selection ID")

    return int(identifier)


def select_script(
    files: list[str],
    root: Path,
    weights: dict[str, int],
    theme: dict[str, str],
) -> str | None:
    files.sort(
        key=lambda path: (
            -weights.get(path, 0),
            os.path.relpath(path, root).casefold(),
            path,
        )
    )

    accent = ansi(theme["accent"])
    foreground = ansi(theme["fg"])
    warning = ansi(theme["warning"])
    rows: list[str] = []

    for index, path in enumerate(files):
        relative = os.path.relpath(path, root)
        directory, filename = os.path.split(relative)
        prefix = visible(directory) + "/" if directory else ""

        label = (
            f"{foreground}{prefix}{RESET}"
            f"{BOLD}{accent}{visible(filename)}{RESET}"
        )

        weight = weights.get(path, 0)
        if weight:
            label += f"  {warning}★ {weight}{RESET}"

        # Real filenames are retrieved by ID, never from decorated text.
        rows.append(f"{index}\t{label}")

    with tempfile.TemporaryDirectory(
        prefix="dusky-scripts-"
    ) as temporary:
        snapshot = Path(temporary) / "preview.json"
        snapshot.write_text(
            json.dumps(
                {
                    "files": files,
                    "theme": theme,
                    "weights": weights,
                },
                ensure_ascii=True,
            ),
            encoding="utf-8",
        )

        preview_environment = {
            "DUSKY_SCRIPTS_PYTHON": sys.executable,
            "DUSKY_SCRIPTS_HELPER": os.path.abspath(__file__),
            "DUSKY_SCRIPTS_SNAPSHOT": str(snapshot),
        }

        preview_base = (
            '"$DUSKY_SCRIPTS_PYTHON" '
            '"$DUSKY_SCRIPTS_HELPER" '
        )
        source_preview = (
            preview_base
            + '--preview "$DUSKY_SCRIPTS_SNAPSHOT" {1}'
        )
        details_preview = (
            preview_base
            + '--preview-details "$DUSKY_SCRIPTS_SNAPSHOT" {1}'
        )

        index = choose(
            rows,
            [
                "--with-shell=bash -c",
                "--style=full",
                "--height=100%",
                "--highlight-line",
                "--border=rounded",
                "--border-label= 󰟆 dusky scripts ",
                "--border-label-pos=center",
                "--input-border=rounded",
                "--list-border=rounded",
                "--ghost=Search user scripts…",
                "--info=inline-right",
                "--no-hscroll",
                "--tabstop=4",
                "--preview=" + source_preview,
                "--preview-window=right,50%,border-rounded,wrap",
                "--preview-label= Alt-I: details · Alt-S: source ",
                "--bind=ctrl-p:toggle-preview",
                "--bind=pgup:preview-page-up",
                "--bind=pgdn:preview-page-down",
                "--bind=shift-up:preview-up",
                "--bind=shift-down:preview-down",
                "--bind=alt-i:change-preview(" + details_preview + ")",
                "--bind=alt-s:change-preview(" + source_preview + ")",
                "--bind=esc:abort",
                "--bind=enter:accept",
            ],
            theme,
            rank_matches=False,
            extra_environment=preview_environment,
        )

    if index is None:
        return None
    if not 0 <= index < len(files):
        raise FinderError("Selected script ID is out of range")

    return files[index]


def human_size(size: int) -> str:
    value = float(size)

    for unit in ("B", "KiB", "MiB", "GiB", "TiB"):
        if value < 1024 or unit == "TiB":
            return f"{size} B" if unit == "B" else f"{value:.2f} {unit}"
        value /= 1024

    raise AssertionError("unreachable")


def preview_file(
    snapshot: str,
    identifier: str,
    show_details: bool = False,
) -> int:
    try:
        data = json.loads(Path(snapshot).read_text(encoding="utf-8"))
        index = int(identifier)
        files = data["files"]

        if not 0 <= index < len(files):
            raise ValueError("preview ID is out of range")

        path = files[index]
        theme = data["theme"]
        weight = data["weights"].get(path, 0)
        metadata = os.stat(path)

        if not stat.S_ISREG(metadata.st_mode):
            raise OSError("selected path is no longer a regular file")

        foreground = ansi(theme["fg"])

        if show_details:
            accent = ansi(theme["accent"])
            warning = ansi(theme["warning"])

            modified = datetime.fromtimestamp(
                metadata.st_mtime
            ).astimezone().strftime("%Y-%m-%d %H:%M:%S %Z")

            if os.access(path, os.X_OK):
                runner = "Direct execution / file shebang"
            elif path.endswith(".py"):
                runner = "python"
            else:
                runner = "bash"

            with open(path, "rb") as source:
                first_line = source.readline(4096)

            shebang = ""
            if first_line.startswith(b"#!"):
                shebang = first_line.rstrip(b"\r\n").decode(
                    "utf-8", errors="replace"
                )

            print(
                f"{BOLD}{accent}"
                f"{visible(os.path.basename(path))}{RESET}"
            )
            print(
                f"{foreground}{visible(home_path(path))}{RESET}\n"
            )

            fields = [
                ("Size", human_size(metadata.st_size)),
                ("Modified", modified),
                ("Permissions", stat.filemode(metadata.st_mode)),
                ("Run with", runner),
                ("Recorded uses", str(weight)),
            ]

            if shebang:
                fields.append(("Shebang", visible(shebang)))
            if os.path.islink(path):
                fields.append(
                    ("Link target", visible(os.path.realpath(path)))
                )

            for key, value in fields:
                print(
                    f"{accent}{key:<14}{RESET}"
                    f"{foreground}{value}{RESET}"
                )

            print(
                f"\n{BOLD}{warning}"
                f"Source · first {PREVIEW_LINES} lines"
                f"{RESET}\n",
                flush=True,
            )

        bat = shutil.which("bat")

        if bat:
            command = [
                bat,
                "--paging=never",
                "--color=always",
                "--style=numbers",
                f"--line-range=1:{PREVIEW_LINES}",
            ]

            columns = os.environ.get("FZF_PREVIEW_COLUMNS", "")
            if columns.isdecimal() and int(columns) > 0:
                command.append(f"--terminal-width={columns}")

            command.extend(["--", path])
            result = subprocess.run(command, check=False)

            if result.returncode == 0:
                return 0
            if result.returncode < 0:
                return 128 - result.returncode

        # Fallback remains bounded and uses Matugen's foreground.
        print(foreground, end="")
        try:
            with open(path, encoding="utf-8", errors="replace") as source:
                for number, line in enumerate(source, 1):
                    if number > PREVIEW_LINES:
                        break
                    text = line.rstrip("\n").expandtabs(4)
                    print(f"{number:4}  {visible(text)}")
        finally:
            print(RESET, end="")

        return 0

    except (
        OSError,
        ValueError,
        KeyError,
        TypeError,
        OverflowError,
    ) as exc:
        print(f"Preview unavailable: {exc}", file=sys.stderr)
        return 1


def run_command(path: str) -> tuple[list[str], str]:
    portable = quoted_path(path)

    if os.access(path, os.X_OK):
        return [path], portable

    if path.endswith(".py"):
        interpreter = "python"
    elif path.endswith(".sh"):
        interpreter = "bash"
    else:
        raise FinderError(f"No interpreter rule for {path}")

    executable = require(interpreter)
    return [executable, path], f"{interpreter} {portable}"


def copy_text(text: str) -> None:
    candidates: list[list[str]] = []

    wayland = shutil.which("wl-copy")
    if wayland:
        candidates.append([wayland, "--type", "text/plain"])

    xclip = shutil.which("xclip")
    if xclip and os.environ.get("DISPLAY"):
        candidates.append([xclip, "-selection", "clipboard"])

    if not candidates:
        raise FinderError(
            "No clipboard utility available. Install wl-clipboard "
            "for Wayland, or use xclip with an X11 display."
        )

    failures: list[str] = []

    for command in candidates:
        tool = Path(command[0]).name

        # Clipboard tools can fork. Do not capture their stderr through
        # a pipe that a background clipboard process could keep open.
        with tempfile.TemporaryFile() as errors:
            try:
                result = subprocess.run(
                    command,
                    input=os.fsencode(text),
                    stdout=subprocess.DEVNULL,
                    stderr=errors,
                    timeout=10,
                    check=False,
                )
            except subprocess.TimeoutExpired:
                failures.append(f"{tool}: timed out")
                continue
            except OSError as exc:
                failures.append(f"{tool}: {exc}")
                continue

            if result.returncode == 0:
                return

            errors.seek(0)
            detail = errors.read(4096).decode(
                "utf-8", errors="replace"
            ).strip()

            failures.append(
                f"{tool}: "
                + (detail or f"exit status {result.returncode}")
            )

    raise FinderError(
        "Clipboard copying failed:\n" + "\n".join(failures)
    )


def queue_command(buffer_file: Path, command: str) -> None:
    buffer_file.write_bytes(os.fsencode(command) + b"\0")


def normalized_status(code: int) -> int:
    return 128 - code if code < 0 else code


def launch(command: list[str], path: str) -> int:
    # Preserve the caller's working directory and terminal streams.
    process = subprocess.Popen(command)

    try:
        return normalized_status(process.wait())
    finally:
        record_use(path)


def select_action(
    path: str,
    theme: dict[str, str],
) -> int | None:
    rows = [
        "1\t1. 󰜎 Run & copy path (Default)",
        "2\t2.  Type in terminal (don't run)",
        "3\t3.  Copy to clipboard",
        "4\t4. 󰆧 Copy & type in terminal",
        "5\t5.  Edit in Neovim",
        "6\t6. 󰉋 Reveal in Yazi",
    ]

    action = choose(
        rows,
        [
            "--style=minimal",
            "--height=~40%",
            "--border=rounded",
            "--border-label= 󰢱 Action ",
            "--border-label-pos=center",
            "--header= Target: " + visible(home_path(path)),
            "--header-border=horizontal",
            "--ghost=Search actions…",
            "--info=hidden",
            "--scheme=default",
            "--tiebreak=begin,length,index",
            "--bind=esc:abort",
            "--bind=enter:accept",
        ],
        theme,
        # Unlike the browser, actions must rerank when searched.
        rank_matches=True,
        shortcuts=ACTION_KEYS,
    )

    if action is not None and action not in range(1, 7):
        raise FinderError("Selected action ID is out of range")

    return action


def perform_action(
    path: str,
    action: int,
    buffer_file: Path,
) -> int:
    if not os.path.isfile(path):
        raise FinderError(
            f"The selected file no longer exists: {path}"
        )

    if action in (1, 2, 4):
        command, command_text = run_command(path)

    if action == 1:
        # Copy before launch, so the path is available while the program
        # is running. Clipboard failure must not block execution.
        try:
            copy_text(quoted_path(path))
        except FinderError as exc:
            warn(f"{exc}\nRunning without clipboard confirmation.")

        print(
            f"\nRunning: {visible(command_text)}\n",
            flush=True,
        )
        return launch(command, path)

    if action == 2:
        queue_command(buffer_file, command_text)
        record_use(path)
        return 0

    if action == 3:
        text = quoted_path(path)
        copy_text(text)
        record_use(path)
        print(f"Copied: {visible(text)}")
        return 0

    if action == 4:
        # Queue first so a clipboard failure cannot discard the command.
        queue_command(buffer_file, command_text)
        record_use(path)

        try:
            copy_text(quoted_path(path))
        except FinderError as exc:
            warn(
                f"{exc}\n"
                "The run command is still queued for the prompt."
            )
            return 1

        print("Copied the path and queued the run command.")
        return 0

    if action == 5:
        return launch([require("nvim"), "--", path], path)

    if action == 6:
        return launch([require("yazi"), path], path)

    raise FinderError("Unknown action")


def main() -> int:
    if len(sys.argv) == 4 and sys.argv[1] in {
        "--preview",
        "--preview-details",
    }:
        return preview_file(
            sys.argv[2],
            sys.argv[3],
            show_details=sys.argv[1] == "--preview-details",
        )

    parser = argparse.ArgumentParser(
        description="Find, preview, run, edit, and copy your scripts."
    )
    parser.add_argument(
        "--root",
        default=str(DEFAULT_ROOT),
        help="directory to search; default: ~/user_scripts",
    )
    parser.add_argument(
        "--buffer-file",
        required=True,
        help="private command-transfer file supplied by the Zsh wrapper",
    )
    arguments = parser.parse_args()

    require("fzf")
    require("bash")

    try:
        with open("/dev/tty", "r+b", buffering=0):
            pass
    except OSError as exc:
        raise FinderError(
            "The scripts finder requires a controlling terminal"
        ) from exc

    root = Path(os.path.abspath(os.path.expanduser(arguments.root)))
    buffer_file = Path(arguments.buffer_file)

    theme = load_theme()
    files = discover(root)

    try:
        weights = read_weights()
    except FinderError as exc:
        warn(
            f"{exc}\n"
            "Using alphabetical ordering for this invocation."
        )
        weights = {}

    path = select_script(files, root, weights, theme)
    if path is None:
        return 0

    action = select_action(path, theme)
    if action is None:
        return 0

    return perform_action(path, action, buffer_file)


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except KeyboardInterrupt:
        raise SystemExit(130)
    except (FinderError, OSError, ValueError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        raise SystemExit(1)
