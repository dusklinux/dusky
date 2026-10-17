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

Preview layout (orientation, percentage, visibility):
    ~/.config/dusky/settings/scripts_fzf/preview_layout

Browser:
    Enter          Open action menu
    Ctrl-P         Toggle preview
    Alt-F          Show the full script
    Alt-Arrows     Resize preview by 5% along its current axis
    Alt-H/J/K/L    Move preview left / down / up / right
    Mouse drag     Resize the preview using its left border
    Page Up/Down   Scroll preview
    Shift-Up/Down  Scroll preview one line
    Esc            Cancel

Descriptions:
    Write the description as the first comment block after the shebang.
    A blank line or blank comment ends the block. Description: is optional.
    Python encoding declarations are skipped; module docstrings also work.
    An explicit # Description: header takes priority over other header comments.
    The preview shows the description, total line count, and highlighted source.
    Source rendering starts with 500 lines; load the full script on demand.

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

lazy import argparse
lazy import ast
lazy import fcntl
lazy import io
lazy from inspect import cleandoc
import json
import os
from pathlib import Path
import re
import shlex
import shutil
import stat
import subprocess
import sys
lazy import tempfile
lazy import tokenize


HOME = Path.home()
DEFAULT_ROOT = HOME / "user_scripts"
THEME_FILE = HOME / ".config/matugen/generated/dusky_tui.json"

STATE_DIR = HOME / ".config/dusky/settings/scripts_fzf"
STATE_FILE = STATE_DIR / "state.json"
LEGACY_STATE = STATE_DIR / "state"
LOCK_FILE = STATE_DIR / "state.lock"
LAYOUT_FILE = STATE_DIR / "preview_layout"

THEME_KEYS = ("bg", "fg", "accent", "error", "warning", "success", "muted")

ACTION_KEYS = {
    "alt-r": 1,
    "alt-t": 2,
    "alt-c": 3,
    "alt-b": 4,
    "alt-e": 5,
    "alt-y": 6,
}

DESCRIPTION_BYTES = 64 * 1024
PREVIEW_LINES = 500
# Same directory-group palette as the Git file browser.
DIR_PALETTE = (111, 176, 215, 114, 221, 116, 211, 147, 180, 152, 182, 186)
RESET = "\033[0m"
BOLD = "\033[1m"


# fzf runs this in its existing Bash transform process: no extra interpreter.
# State is private to one picker; synchronous transforms preserve key order.
PREVIEW_LAYOUT_TRANSFORM = r"""
IFS=' ' read -r edge percent visibility < "$DUSKY_SCRIPTS_LAYOUT" || { printf bell; exit; }
case $edge in
    left|right) pane=${FZF_PREVIEW_COLUMNS:-0}; span=${FZF_COLUMNS:-0}; chrome=4 ;;
    up|down)    pane=${FZF_PREVIEW_LINES:-0}; span=${FZF_LINES:-0}; chrome=2 ;;
esac
if (( pane > 0 && span > 0 )); then
    # Rounded preview borders occupy four columns or two rows. FZF_COLUMNS
    # and FZF_LINES already exclude the outer frame. Allow one rounding cell;
    # a larger difference means the mouse or terminal constraints changed it.
    actual=$((pane + chrome))
    expected=$((span * percent / 100))
    if (( actual < expected || actual > expected + 1 )); then
        percent=$(((actual * 100 + span / 2) / span))
    fi
fi
current_percent=$percent
if [[ $layout_action == move ]]; then
    edge=$layout_direction
    visibility=visible
elif [[ $layout_action == resize ]]; then
    (( pane > 0 )) || exit 0
    case "$edge:$layout_direction" in
        right:left|left:right|up:down|down:up) delta=5 ;;
        right:right|left:left|up:up|down:down) delta=-5 ;;
        *) exit 0 ;;
    esac
    percent=$((percent + delta))
fi
(( percent < 10 )) && percent=10
(( percent > 90 )) && percent=90
[[ $layout_action == resize && $percent == "$current_percent" ]] && exit 0
case $layout_action in
    toggle) [[ $visibility == hidden ]] && visibility=visible || visibility=hidden ;;
    full) visibility=visible ;;
esac
printf '%s %s %s\n' "$edge" "$percent" "$visibility" > "$DUSKY_SCRIPTS_LAYOUT" || { printf bell; exit; }
case $layout_action in
    capture|toggle) printf '%s' "$layout_result" ;;
    full) printf '%s' "$DUSKY_SCRIPTS_EXPAND" ;;
    *) printf 'show-preview+change-preview-window(%s,%s%%,border-rounded,wrap)+refresh-preview' "$edge" "$percent" ;;
esac
"""


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


def read_preview_layout(path: Path) -> tuple[str, int, str]:
    edge, percentage, visibility = path.read_text(encoding="ascii").split()
    percent = int(percentage)
    if (
        edge not in {"left", "right", "up", "down"}
        or not 10 <= percent <= 90
        or visibility not in {"visible", "hidden"}
    ):
        raise ValueError("expected: left/right/up/down, 10–90, visible/hidden")
    return edge, percent, visibility


def save_preview_layout(session_layout: Path) -> None:
    temporary: str | None = None
    try:
        edge, percent, visibility = read_preview_layout(session_layout)
        STATE_DIR.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(
            mode="w", encoding="ascii", dir=STATE_DIR,
            prefix=".preview-layout-", delete=False,
        ) as output:
            temporary = output.name
            output.write(f"{edge} {percent} {visibility}\n")
        # Concurrent pickers use private session state. The last closed picker
        # wins, and readers always see one complete preference record.
        os.replace(temporary, LAYOUT_FILE)
        temporary = None
    except (OSError, ValueError) as exc:
        warn(f"Could not save preview layout: {exc}")
    finally:
        if temporary is not None:
            try:
                os.unlink(temporary)
            except OSError:
                pass


def discover(root: Path) -> list[str]:
    if not root.is_dir():
        raise FinderError(f"Scripts directory does not exist: {root}")

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
        "--sort",
        "--algo=v2",
        "--cycle",
        "--layout=reverse",
        "--border=rounded",
        "--delimiter=\t",
        "--with-nth=2..",
        "--ellipsis=",
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
    relative_paths = {path: os.path.relpath(path, root) for path in files}
    files.sort(
        key=lambda path: (
            -weights.get(path, 0),
            relative_paths[path].casefold(),
            path,
        )
    )

    accent = ansi(theme["accent"])
    groups = sorted({
        os.sep.join(relative.split(os.sep)[:-1][:2])
        for relative in relative_paths.values()
    })
    directory_colors = {
        group: f"\033[38;5;{DIR_PALETTE[index % len(DIR_PALETTE)]}m"
        for index, group in enumerate(groups)
    }
    warning = ansi(theme["warning"])
    rows: list[str] = []

    for index, path in enumerate(files):
        relative = relative_paths[path]
        directory, filename = os.path.split(relative)
        parts = directory.split(os.sep) if directory else []
        group = os.sep.join(parts[:2])
        prefix = visible(group) + "/" if group else ""
        if len(parts) > 2:
            prefix += "\033[2m" + visible(os.sep.join(parts[2:])) + "/"

        label = (
            f"{directory_colors[group]}{prefix}{RESET}"
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
                },
                ensure_ascii=True,
            ),
            encoding="utf-8",
        )

        try:
            edge, percent, visibility = read_preview_layout(LAYOUT_FILE)
        except FileNotFoundError:
            edge, percent, visibility = "right", 50, "visible"
        except (OSError, ValueError) as exc:
            warn(f"Cannot read preview layout {LAYOUT_FILE}: {exc}; using defaults")
            edge, percent, visibility = "right", 50, "visible"
        layout = Path(temporary) / "layout"
        layout.write_text(f"{edge} {percent} {visibility}\n", encoding="ascii")
        preview_window = f"{edge},{percent}%,border-rounded,wrap"
        if visibility == "hidden":
            preview_window += ",hidden"
        preview_environment = {
            "DUSKY_SCRIPTS_PYTHON": sys.executable,
            "DUSKY_SCRIPTS_HELPER": os.path.abspath(__file__),
            "DUSKY_SCRIPTS_SNAPSHOT": str(snapshot),
            "DUSKY_SCRIPTS_LAYOUT": str(layout),
        }

        preview_base = (
            '"$DUSKY_SCRIPTS_PYTHON" '
            '"$DUSKY_SCRIPTS_HELPER" '
        )
        preview = (
            preview_base
            + '--preview "$DUSKY_SCRIPTS_SNAPSHOT" {1}'
        )

        full_preview = (
            preview_base
            + '--preview-full "$DUSKY_SCRIPTS_SNAPSHOT" {1}'
        )

        preview_environment["DUSKY_SCRIPTS_EXPAND"] = (
            "show-preview+change-preview(" + full_preview + ")"
        )
        layout_bindings = [
            f"--bind={key}:transform:layout_action={action} "
            f"layout_direction={direction}\n{PREVIEW_LAYOUT_TRANSFORM}"
            for key, action, direction in (
                ("alt-h", "move", "left"),
                ("alt-j", "move", "down"),
                ("alt-k", "move", "up"),
                ("alt-l", "move", "right"),
                ("alt-left", "resize", "left"),
                ("alt-right", "resize", "right"),
                ("alt-up", "resize", "up"),
                ("alt-down", "resize", "down"),
            )
        ]

        layout_bindings.extend(
            f"--bind={key}:transform:layout_action={action} "
            f"layout_result={result}\n{PREVIEW_LAYOUT_TRANSFORM}"
            for key, action, result in (
                ("ctrl-p", "toggle", "toggle-preview"),
                ("alt-f", "full", ""),
                ("enter", "capture", "accept"),
                ("esc,ctrl-c,ctrl-g", "capture", "abort"),
            )
        )

        try:
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
                    "--list-border=none",
                    "--marker=",
                    "--ghost=Search user scripts…",
                    "--info=inline-right",
                    "--no-hscroll",
                    "--tabstop=4",
                    "--scheme=path",
                    "--tiebreak=pathname,length,index",
                    "--preview=" + preview,
                    "--preview-window=" + preview_window,
                    "--preview-label= Description + source · Alt-F: full ",
                    "--bind=focus:change-preview(" + preview + ")",
                    "--bind=pgup:preview-page-up",
                    "--bind=pgdn:preview-page-down",
                    "--bind=shift-up:preview-up",
                    "--bind=shift-down:preview-down",
                    *layout_bindings,
                ],
                theme,
                extra_environment=preview_environment,
            )
        finally:
            save_preview_layout(layout)

    if index is None:
        return None
    if not 0 <= index < len(files):
        raise FinderError("Selected script ID is out of range")

    return files[index]


def read_description(path: str) -> str:
    """Read a header description without executing or parsing the whole script."""
    with open(path, "rb") as source:
        header = source.read(DESCRIPTION_BYTES).decode(
            "utf-8-sig", errors="replace"
        )

    description: list[str] = []
    first_comments: list[str] = []
    first_block_ended = False
    for number, line in enumerate(header.splitlines()):
        stripped = line.strip()
        if not stripped or stripped == "#":
            if description:
                break
            if first_comments:
                first_block_ended = True
            continue
        if not stripped.startswith("#"):
            break
        if number == 0 and stripped.startswith("#!"):
            continue
        if number < 2 and path.endswith(".py") and re.prefixmatch(
            r"#.*?coding[:=]\s*[-\w.]+", stripped
        ):
            continue
        comment = stripped[1:].removeprefix(" ")
        if description:
            description.append(comment)
        else:
            match = re.fullmatch(
                r"#\s*Description:\s*(.*)", stripped, re.IGNORECASE
            )
            if match:
                # An empty marker can introduce a multiline description.
                description.append(match[1])
        if not first_block_ended:
            first_comments.append(comment)

    if description:
        return cleandoc("\n".join(description)).strip()
    if first_comments:
        return cleandoc("\n".join(first_comments)).strip()

    if path.endswith(".py"):
        try:
            for token in tokenize.generate_tokens(io.StringIO(header).readline):
                if token.type in {tokenize.COMMENT, tokenize.NL, tokenize.NEWLINE}:
                    continue
                if token.type == tokenize.STRING:
                    value = ast.literal_eval(token.string)
                    if isinstance(value, str):
                        return cleandoc(value).strip()
                break
        except (SyntaxError, ValueError, tokenize.TokenError):
            pass

    return ""


def count_lines(path: str) -> int:
    """Count newline-delimited source lines, including an unterminated last line."""
    total = 0
    last = b""
    with open(path, "rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            total += chunk.count(b"\n")
            last = chunk[-1:]
    return total + bool(last and last != b"\n")


def preview_file(
    snapshot: str, identifier: str, *, full_source: bool = False,
) -> int:
    try:
        data = json.loads(Path(snapshot).read_text(encoding="utf-8"))
        index = int(identifier)
        files = data["files"]

        if not 0 <= index < len(files):
            raise ValueError("preview ID is out of range")

        path = files[index]
        theme = data["theme"]
        metadata = os.stat(path)

        if not stat.S_ISREG(metadata.st_mode):
            raise OSError("selected path is no longer a regular file")

        foreground = ansi(theme["fg"])

        accent = ansi(theme["accent"])
        description = read_description(path)
        total_lines = count_lines(path)
        print(f"{BOLD}{accent}Description{RESET}")
        if description:
            for line in description.splitlines():
                print(f"{accent}{visible(line.expandtabs(4))}{RESET}")
        else:
            print(f"{foreground}No description provided.{RESET}")
        unit = "line" if total_lines == 1 else "lines"
        source_label = f"Source · {total_lines:,} {unit}"
        limited = not full_source and total_lines > PREVIEW_LINES
        if limited:
            source_label += f" · first {PREVIEW_LINES} shown"
        print(f"\n{BOLD}{accent}{source_label}{RESET}\n", flush=True)

        more_hint = (
            f"\n{accent}More source available. Press Alt-F for full script.{RESET}"
        )
        bat = shutil.which("bat")

        if bat:
            command = [
                bat,
                "--paging=never",
                "--color=always",
                "--style=numbers",
            ]

            if not full_source:
                command.append(f"--line-range=1:{PREVIEW_LINES}")

            columns = os.environ.get("FZF_PREVIEW_COLUMNS", "")
            if columns.isdecimal() and int(columns) > 0:
                command.append(f"--terminal-width={columns}")

            command.extend(["--", path])
            result = subprocess.run(command, check=False)

            if result.returncode == 0:
                if limited:
                    print(more_hint)
                return 0
            if result.returncode < 0:
                return 128 - result.returncode

        # The plain-text fallback follows the same source limit.
        print(foreground, end="")
        try:
            with open(path, encoding="utf-8", errors="replace") as source:
                for number, line in enumerate(source, 1):
                    if not full_source and number > PREVIEW_LINES:
                        break
                    text = line.rstrip("\n").expandtabs(4)
                    print(f"{number:4}  {visible(text)}")
        finally:
            print(RESET, end="")

        if limited:
            print(more_hint)
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
    if len(sys.argv) == 4 and sys.argv[1] in {"--preview", "--preview-full"}:
        return preview_file(
            sys.argv[2], sys.argv[3], full_source=sys.argv[1] == "--preview-full",
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
