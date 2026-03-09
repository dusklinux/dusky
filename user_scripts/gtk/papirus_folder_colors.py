#!/usr/bin/env python3

import argparse
import os
import pwd
import re
import signal
import subprocess
import sys
import tempfile
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import BinaryIO, Final, Never

type RGB = tuple[int, int, int]

DEFAULT_CSS_RELATIVE: Final[Path] = Path(".config/matugen/generated/gtk-4.css")

SUDO_BIN: Final[Path] = Path("/usr/bin/sudo")
PAPIRUS_FOLDERS_BIN: Final[Path] = Path("/usr/bin/papirus-folders")
GSETTINGS_BIN: Final[Path] = Path("/usr/bin/gsettings")

PAPIRUS_THEME: Final[str] = "Papirus-Dark"
GSETTINGS_SCHEMA: Final[str] = "org.gnome.desktop.interface"
GSETTINGS_KEY: Final[str] = "icon-theme"

PAPIRUS_TIMEOUT_SECONDS: Final[float] = 120.0
GSETTINGS_TIMEOUT_SECONDS: Final[float] = 10.0

HEX_RE: Final = re.compile(r"#?(?P<value>[0-9A-Fa-f]{6})\Z")
CSS_COMMENT_RE: Final = re.compile(r"/\*.*?\*/", re.DOTALL)
ACCENT_COLOR_RE: Final = re.compile(
    r"^\s*@define-color\s+accent_color\s+(#[0-9A-Fa-f]{6})\s*;\s*$",
    re.MULTILINE,
)

PAPIRUS_COLORS: Final[dict[str, str]] = {
    "adwaita": "93c0ea",
    "black": "4f4f4f",
    "blue": "5294e2",
    "bluegrey": "607d8b",
    "breeze": "57b8ec",
    "brown": "ae8e6c",
    "carmine": "a30002",
    "cyan": "00bcd4",
    "darkcyan": "45abb7",
    "deeporange": "eb6637",
    "green": "87b158",
    "grey": "8e8e8f",
    "indigo": "5c6bc0",
    "magenta": "ca71df",
    "nordic": "81a1c1",
    "orange": "ee923a",
    "palebrown": "d1bfae",
    "paleorange": "eeca8f",
    "pink": "f06292",
    "red": "e25252",
    "teal": "16a085",
    "violet": "7e57c2",
    "white": "e4e4e4",
    "yaru": "676767",
    "yellow": "f9bd30",
}


@dataclass(frozen=True, slots=True)
class UserContext:
    name: str
    uid: int
    gid: int
    home: Path
    supplementary_groups: tuple[int, ...]

    @property
    def runtime_dir(self) -> Path:
        return Path("/run/user") / str(self.uid)


@dataclass(frozen=True, slots=True)
class ExecResult:
    returncode: int
    output: str


def fail(message: str, exit_code: int = 1) -> Never:
    print(message, file=sys.stderr)
    raise SystemExit(exit_code)


def warn(message: str) -> None:
    print(message, file=sys.stderr)


def normalize_hex(value: str) -> str:
    match = HEX_RE.fullmatch(value.strip())
    if match is None:
        raise ValueError(f"Invalid 6-digit hex color: {value!r}")
    return f"#{match.group('value').lower()}"


def hex_to_rgb(value: str) -> RGB:
    hex_value = normalize_hex(value)[1:]
    return (
        int(hex_value[0:2], 16),
        int(hex_value[2:4], 16),
        int(hex_value[4:6], 16),
    )


PAPIRUS_RGBS: Final[dict[str, RGB]] = {
    name: hex_to_rgb(hex_value) for name, hex_value in PAPIRUS_COLORS.items()
}


def build_user_context(name: str, uid: int, gid: int, home: str) -> UserContext:
    try:
        groups = os.getgrouplist(name, gid)
    except OSError:
        groups = [gid]

    supplementary_groups = tuple(sorted({group for group in groups if group != gid}))

    return UserContext(
        name=name,
        uid=uid,
        gid=gid,
        home=Path(home),
        supplementary_groups=supplementary_groups,
    )


def detect_user_context() -> UserContext:
    if os.geteuid() == 0 and (sudo_user := os.environ.get("SUDO_USER")):
        try:
            pw = pwd.getpwnam(sudo_user)
        except KeyError:
            fail(f"Error: could not resolve SUDO_USER account: {sudo_user}")
        return build_user_context(sudo_user, pw.pw_uid, pw.pw_gid, pw.pw_dir)

    pw = pwd.getpwuid(os.getuid())
    return build_user_context(pw.pw_name, pw.pw_uid, pw.pw_gid, pw.pw_dir)


def default_css_file_for(user: UserContext) -> Path:
    return user.home / DEFAULT_CSS_RELATIVE


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Apply the closest Papirus-Dark folder color to a GTK accent color.",
        epilog=(
            "Examples:\n"
            "  papirus_folder_colors.py\n"
            "  papirus_folder_colors.py 0e973f\n"
            "  papirus_folder_colors.py '#0e973f'\n"
            "  papirus_folder_colors.py ~/.config/matugen/generated/gtk-4.css\n\n"
            "Note: an unquoted # starts a shell comment, so '#0e973f' must be quoted."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "target",
        nargs="?",
        help="A 6-digit hex color or a GTK CSS file path.",
    )
    return parser.parse_args(argv)


def extract_accent_hex_from_css(path: Path) -> str:
    if not path.is_file():
        fail(f"Error: file not found: {path}")

    try:
        content = path.read_text(encoding="utf-8")
    except UnicodeDecodeError as exc:
        fail(f"Error: file is not valid UTF-8: {path} ({exc})")
    except OSError as exc:
        fail(f"Error: could not read file: {path} ({exc})")

    content = CSS_COMMENT_RE.sub("", content)
    matches = ACCENT_COLOR_RE.findall(content)

    if not matches:
        fail(
            "Error: could not find an active line like "
            "'@define-color accent_color #xxxxxx;' in the CSS file."
        )

    return matches[-1].lower()


def resolve_target_to_hex(target: str | None, default_css_file: Path) -> str:
    using_default_target = target is None
    resolved_target = str(default_css_file) if using_default_target else target

    path = Path(resolved_target).expanduser()
    if path.is_file():
        return extract_accent_hex_from_css(path)

    try:
        return normalize_hex(resolved_target)
    except ValueError:
        if using_default_target or resolved_target == str(default_css_file):
            fail(
                f"Error: default CSS file was not found: {default_css_file}\n"
                "Pass a hex color like '0e973f' or a valid CSS file path."
            )
        fail(
            f"Error: {resolved_target!r} is neither an existing file nor a valid 6-digit hex color."
        )


def perceptual_distance(left: RGB, right: RGB) -> int:
    dr = left[0] - right[0]
    dg = left[1] - right[1]
    db = left[2] - right[2]
    return 30 * dr * dr + 59 * dg * dg + 11 * db * db


def find_closest_papirus_color(target_hex: str) -> str:
    target_rgb = hex_to_rgb(target_hex)
    return min(
        PAPIRUS_RGBS,
        key=lambda name: perceptual_distance(PAPIRUS_RGBS[name], target_rgb),
    )


def read_captured_output(stream: BinaryIO) -> str:
    stream.flush()
    stream.seek(0)
    data = stream.read()
    return data.decode("utf-8", errors="replace").strip()


def terminate_process_group(process: subprocess.Popen) -> None:
    try:
        os.killpg(process.pid, signal.SIGTERM)
    except ProcessLookupError:
        return
    except PermissionError:
        try:
            process.terminate()
        except ProcessLookupError:
            return

    try:
        process.wait(timeout=2)
        return
    except subprocess.TimeoutExpired:
        pass

    try:
        os.killpg(process.pid, signal.SIGKILL)
    except ProcessLookupError:
        return
    except PermissionError:
        try:
            process.kill()
        except ProcessLookupError:
            return

    try:
        process.wait(timeout=2)
    except subprocess.TimeoutExpired:
        pass


def run_command_attached_or_fail(args: Sequence[str], *, timeout: float) -> None:
    try:
        process = subprocess.Popen(
            list(args),
            start_new_session=True,
        )
    except FileNotFoundError as exc:
        missing = exc.filename or args[0]
        fail(f"Error: required executable was not found: {missing}")
    except OSError as exc:
        fail(f"Error: could not run command: {exc}")

    try:
        returncode = process.wait(timeout=timeout)
    except subprocess.TimeoutExpired:
        terminate_process_group(process)
        fail(f"Error: command timed out after {timeout:.0f}s")
    except OSError as exc:
        terminate_process_group(process)
        fail(f"Error: could not wait for command: {exc}")

    if returncode != 0:
        fail(f"Error: command failed with exit code {returncode}")


def run_command_logged_best_effort(
    args: Sequence[str],
    *,
    timeout: float,
    warning_context: str,
    env: dict[str, str] | None = None,
    user: int | None = None,
    group: int | None = None,
    extra_groups: Sequence[int] | None = None,
) -> ExecResult | None:
    with tempfile.TemporaryFile(mode="w+b") as output_file:
        popen_kwargs: dict[str, object] = {
            "stdin": subprocess.DEVNULL,
            "stdout": output_file,
            "stderr": subprocess.STDOUT,
            "env": env,
            "start_new_session": True,
        }

        if user is not None:
            popen_kwargs["user"] = user
        if group is not None:
            popen_kwargs["group"] = group
        if extra_groups is not None:
            popen_kwargs["extra_groups"] = list(extra_groups)

        try:
            process = subprocess.Popen(list(args), **popen_kwargs)
        except FileNotFoundError as exc:
            missing = exc.filename or args[0]
            warn(f"Warning: {warning_context}: required executable was not found: {missing}")
            return None
        except OSError as exc:
            warn(f"Warning: {warning_context}: {exc}")
            return None

        try:
            returncode = process.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            terminate_process_group(process)
            output = read_captured_output(output_file)
            message = f"Warning: {warning_context}: command timed out after {timeout:.0f}s"
            if output:
                message += f"\nCommand output:\n{output}"
            warn(message)
            return None
        except OSError as exc:
            terminate_process_group(process)
            warn(f"Warning: {warning_context}: {exc}")
            return None

        return ExecResult(returncode=returncode, output=read_captured_output(output_file))


def run_noninteractive_sudo_or_fail(args: Sequence[str], *, timeout: float) -> None:
    with tempfile.TemporaryFile(mode="w+b") as output_file:
        try:
            process = subprocess.Popen(
                list(args),
                stdin=subprocess.DEVNULL,
                stdout=output_file,
                stderr=subprocess.STDOUT,
                start_new_session=True,
            )
        except FileNotFoundError as exc:
            missing = exc.filename or args[0]
            fail(f"Error: required executable was not found: {missing}")
        except OSError as exc:
            fail(f"Error: could not run command: {exc}")

        try:
            returncode = process.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            terminate_process_group(process)
            output = read_captured_output(output_file)
            message = f"Error: command timed out after {timeout:.0f}s"
            if output:
                message += f"\nCommand output:\n{output}"
            fail(message)
        except OSError as exc:
            terminate_process_group(process)
            fail(f"Error: could not wait for command: {exc}")

        if returncode != 0:
            detail = read_captured_output(output_file) or f"exit code {returncode}"
            fail(f"Error: papirus-folders failed: {detail}")


def build_user_session_env(user: UserContext) -> dict[str, str]:
    env = os.environ.copy()
    env["HOME"] = str(user.home)
    env["USER"] = user.name
    env["LOGNAME"] = user.name

    runtime_dir = user.runtime_dir
    if runtime_dir.is_dir():
        env["XDG_RUNTIME_DIR"] = str(runtime_dir)
        bus = runtime_dir / "bus"
        if bus.exists():
            env["DBUS_SESSION_BUS_ADDRESS"] = f"unix:path={bus}"

    return env


def apply_papirus_color(color_name: str) -> None:
    if not PAPIRUS_FOLDERS_BIN.is_file():
        fail(f"Error: papirus-folders was not found at {PAPIRUS_FOLDERS_BIN}")
    if not os.access(PAPIRUS_FOLDERS_BIN, os.X_OK):
        fail(f"Error: papirus-folders is not executable: {PAPIRUS_FOLDERS_BIN}")

    base_command = [
        str(PAPIRUS_FOLDERS_BIN),
        "-C",
        color_name,
        "--theme",
        PAPIRUS_THEME,
    ]

    if os.geteuid() == 0:
        run_command_attached_or_fail(base_command, timeout=PAPIRUS_TIMEOUT_SECONDS)
        return

    if not SUDO_BIN.is_file() or not os.access(SUDO_BIN, os.X_OK):
        fail(f"Error: sudo was not found at {SUDO_BIN}")

    sudo_command = [str(SUDO_BIN), "--", *base_command]

    if sys.stdin.isatty() and sys.stdout.isatty() and sys.stderr.isatty():
        run_command_attached_or_fail(sudo_command, timeout=PAPIRUS_TIMEOUT_SECONDS)
        return

    noninteractive_command = [str(SUDO_BIN), "-n", "--", *base_command]
    run_noninteractive_sudo_or_fail(noninteractive_command, timeout=PAPIRUS_TIMEOUT_SECONDS)


def refresh_icon_theme(user: UserContext) -> None:
    if not GSETTINGS_BIN.is_file() or not os.access(GSETTINGS_BIN, os.X_OK):
        return

    env = build_user_session_env(user)

    run_as_other_user = os.geteuid() == 0 and user.uid != 0
    child_user = user.uid if run_as_other_user else None
    child_group = user.gid if run_as_other_user else None
    child_extra_groups = user.supplementary_groups if run_as_other_user else None

    current = run_command_logged_best_effort(
        [str(GSETTINGS_BIN), "get", GSETTINGS_SCHEMA, GSETTINGS_KEY],
        timeout=GSETTINGS_TIMEOUT_SECONDS,
        warning_context="could not query current icon theme",
        env=env,
        user=child_user,
        group=child_group,
        extra_groups=child_extra_groups,
    )
    if current is None:
        return
    if current.returncode != 0:
        detail = current.output or f"exit code {current.returncode}"
        warn(f"Warning: could not query current icon theme: {detail}")
        return

    current_theme = current.output.strip().strip("'")
    if current_theme != PAPIRUS_THEME:
        return

    for theme in ("Adwaita", PAPIRUS_THEME):
        result = run_command_logged_best_effort(
            [str(GSETTINGS_BIN), "set", GSETTINGS_SCHEMA, GSETTINGS_KEY, theme],
            timeout=GSETTINGS_TIMEOUT_SECONDS,
            warning_context=f"could not refresh icon theme while setting {theme}",
            env=env,
            user=child_user,
            group=child_group,
            extra_groups=child_extra_groups,
        )
        if result is None:
            return
        if result.returncode != 0:
            detail = result.output or f"exit code {result.returncode}"
            warn(f"Warning: could not refresh icon theme while setting {theme}: {detail}")
            return


def main(argv: Sequence[str] | None = None) -> int:
    user = detect_user_context()
    args = parse_args(argv)

    default_css_file = default_css_file_for(user)
    target_hex = resolve_target_to_hex(args.target, default_css_file)
    closest_color = find_closest_papirus_color(target_hex)

    print(f"Target accent: {target_hex}")
    print(f"Applying {PAPIRUS_THEME} folder color: {closest_color}")

    apply_papirus_color(closest_color)
    refresh_icon_theme(user)

    print("Folder theme successfully updated.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
