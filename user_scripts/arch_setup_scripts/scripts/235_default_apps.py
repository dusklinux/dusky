#!/usr/bin/env python3
#d: Select default applications or restore saved choices after an update
"""Dusky default apps. Python 3.15+ standard library only; run as the desktop user.

Menus: --file-manager, --browser, --text-editor, --terminal.
Direct selection: --terminal --set foot. Restore: --apply-state (alias --auto).
Selections synchronize Hyprland bindings, user MIME defaults, terminal launchers,
and native FileManager1 activation. No Dusky background process is installed.
"""

from __future__ import annotations

import argparse
from configparser import ConfigParser, Error as ConfigError
from contextlib import contextmanager
from dataclasses import dataclass
import fcntl
import os
from pathlib import Path
import pwd
import re
import shlex
import shutil
import signal
import stat
import subprocess
import sys
import tempfile
import time
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Iterator, Mapping, Sequence

type Token = re.Match[str]

# Read process umask once at startup without permanently mutating it.
UMASK: int = os.umask(0o022)
os.umask(UMASK)
DEFAULT_FILE_MODE: int = 0o666 & ~UMASK


@dataclass(frozen=True, slots=True)
class App:
    key: str
    desktop: str
    label: str
    terminal: bool = False
    commands: tuple[str, ...] = ()

    @property
    def candidates(self) -> tuple[str, ...]:
        return self.commands if self.commands else (self.key,)

    @property
    def command(self) -> str:
        return next((name for name in self.candidates if shutil.which(name)), self.candidates[0])

    @property
    def is_installed(self) -> bool:
        return any(shutil.which(name) is not None for name in self.candidates)


@dataclass(frozen=True, slots=True)
class Category:
    variable: str
    description: str
    binding: str
    state: str
    legacy: tuple[str, str]  # (true_choice, false_choice)
    apps: tuple[App, ...]

    def find_app(self, key: str) -> App | None:
        cleaned = key.strip()
        if not cleaned or cleaned == "unknown":
            return None
        desktop_lower = cleaned.casefold()
        for app in self.apps:
            if cleaned == app.key or cleaned in app.commands or desktop_lower == app.desktop.casefold():
                return app
        exe = safe_first_word(cleaned, fallback="")
        if exe and exe != cleaned:
            for app in self.apps:
                if exe == app.key or exe in app.commands:
                    return app
        return None

    def app(self, key: str) -> App:
        if found := self.find_app(key):
            return found
        raise ValueError(f"Unknown {self.description.lower()} choice: {key!r}")


# Shared catalog for CLI flags, interactive menus, and automatic state restoration.
CATEGORIES: frozendict[str, Category] = frozendict({
    "file-manager": Category(
        "fileManager",
        "File Manager",
        "SUPER + E",
        "filemanager_switch",
        ("yazi", "nemo"),
        (
            App("nemo", "nemo.desktop", "Nemo (GUI)", False),
            App("yazi", "yazi.desktop", "Yazi (Terminal)", True),
            App("thunar", "thunar.desktop", "Thunar (GUI)", False),
            App("dolphin", "org.kde.dolphin.desktop", "Dolphin (GUI)", False),
            App("nautilus", "org.gnome.Nautilus.desktop", "Nautilus (GUI)", False),
            App("pcmanfm", "pcmanfm.desktop", "PCManFM (GUI)", False),
            App("ranger", "ranger.desktop", "Ranger (Terminal)", True),
            App("lf", "lf.desktop", "Lf (Terminal)", True),
            App("superfile", "superfile.desktop", "Superfile (Terminal)", True, ("spf", "superfile")),
        ),
    ),
    "browser": Category(
        "browser",
        "Launch Browser",
        "SUPER + W",
        "browser_switch",
        ("firefox", "chromium"),
        (
            App("firefox", "firefox.desktop", "Firefox", False),
            App("chromium", "chromium.desktop", "Chromium", False),
            App("zen-browser", "zen.desktop", "Zen Browser", False),
            App("google-chrome-stable", "google-chrome.desktop", "Google Chrome", False),
            App("brave", "brave-browser.desktop", "Brave", False),
            App("helium-browser", "helium.desktop", "Helium", False),
            App("librewolf", "librewolf.desktop", "LibreWolf", False),
            App("edge", "microsoft-edge.desktop", "Microsoft Edge", False, ("microsoft-edge-stable", "edge")),
            App("vivaldi", "vivaldi-stable.desktop", "Vivaldi", False),
            App("qutebrowser", "org.qutebrowser.qutebrowser.desktop", "Qutebrowser", False),
            App("lynx", "lynx.desktop", "Lynx (Terminal)", True),
        ),
    ),
    "text-editor": Category(
        "textEditor",
        "Open Text Editor",
        "SUPER + R",
        "texteditor_switch",
        ("nvim", "gnome-text-editor"),
        (
            App("gnome-text-editor", "org.gnome.TextEditor.desktop", "GNOME Text Editor (GUI)", False),
            App("nvim", "nvim.desktop", "Neovim (Terminal)", True),
            App("nano", "nano.desktop", "Nano (Terminal)", True),
            App("code", "code.desktop", "VS Code (GUI)", False),
            App("zeditor", "dev.zed.Zed.desktop", "Zed (GUI)", False),
            App("vscodium", "codium.desktop", "VS Codium (GUI)", False, ("codium", "vscodium")),
            App("helix", "Helix.desktop", "Helix (Terminal)", True, ("helix", "hx")),
            App("kate", "org.kde.kate.desktop", "Kate (GUI)", False),
            App("emacs", "emacs.desktop", "Emacs (GUI)", False),
            App("micro", "micro.desktop", "Micro (Terminal)", True),
            App("mousepad", "org.xfce.mousepad.desktop", "Mousepad (GUI)", False),
        ),
    ),
    "terminal": Category(
        "terminal",
        "Launch Terminal",
        "SUPER + Q",
        "terminal_switch",
        ("kitty", "foot"),
        (
            App("kitty", "kitty.desktop", "Kitty", False),
            App("foot", "foot.desktop", "Foot", False),
            App("alacritty", "Alacritty.desktop", "Alacritty", False),
            App("wezterm", "org.wezfurlong.wezterm.desktop", "WezTerm", False),
            App("ghostty", "com.mitchellh.ghostty.desktop", "Ghostty", False),
            App("konsole", "org.kde.konsole.desktop", "Konsole", False),
            App("gnome-terminal", "org.gnome.Terminal.desktop", "GNOME Terminal", False),
        ),
    ),
})

# Include both shared-mime-info and file(1) MIME types so xdg-open and portals resolve consistently.
MIMES: frozendict[str, tuple[str, ...]] = frozendict({
    "file-manager": ("inode/directory",),
    "browser": (
        "x-scheme-handler/http",
        "x-scheme-handler/https",
        "x-scheme-handler/about",
        "x-scheme-handler/unknown",
        "x-scheme-handler/chrome",
        "x-scheme-handler/ftp",
        "text/html",
        "application/xhtml+xml",
        "application/x-extension-html",
        "application/x-extension-shtml",
        "application/x-extension-xhtml",
        "application/x-extension-xht",
    ),
    "text-editor": (
        "text/plain", "text/markdown", "text/x-markdown", "text/x-shellscript",
        "application/x-shellscript", "application/x-sh", "text/x-sh", "text/x-csh",
        "text/x-zsh", "text/x-fish", "text/x-python", "text/x-script.python",
        "application/x-python-code", "text/x-go", "text/rust", "text/x-rust",
        "text/x-c", "text/x-c++", "text/x-csrc", "text/x-chdr", "text/x-c++src",
        "text/x-c++hdr", "text/x-lua", "text/x-java", "text/x-makefile",
        "text/x-moc", "text/x-pascal", "text/tcl", "text/x-tcl", "text/x-awk",
        "text/x-tex", "application/json", "text/json", "text/x-json",
        "application/toml", "text/x-toml", "application/yaml", "application/x-yaml",
        "text/yaml", "text/x-yaml", "text/css", "text/javascript",
        "application/javascript", "application/x-javascript", "text/xml",
        "application/xml", "application/x-zerosize", "inode/x-empty",
        "text/csv", "text/tab-separated-values", "text/calendar", "text/vcard",
        "application/ld+json", "application/json5", "application/schema+json",
        "application/json-patch+json", "application/x-ndjson", "application/rss+xml",
        "application/atom+xml", "application/rdf+xml", "application/sql",
        "text/x-sql", "application/typescript", "application/x-typescript",
        "text/x-typescript", "application/x-perl", "application/x-ruby",
        "application/x-php", "text/x-perl", "text/x-ruby", "text/x-php",
        "application/x-desktop", "text/x-log", "text/x-patch", "text/x-diff",
        "text/x-cmake", "text/x-meson", "text/x-systemd-unit", "text/x-dbus-service",
        "text/x-ini", "text/x-config", "text/x-properties", "text/x-csharp",
        "text/x-objcsrc", "text/x-objc++src", "text/x-scala", "text/x-kotlin",
        "text/x-sass", "text/x-scss", "text/less", "text/x-less",
        "text/vnd.trolltech.linguist", "application/x-tiled-tsx",
        "text/x-pkgbuild", "text/x-dockerfile", "text/x-nix", "text/x-zig",
        "text/x-haskell", "text/x-literate-haskell", "text/x-lisp",
        "text/x-scheme", "text/x-clojure", "text/x-elixir", "text/x-erlang",
        "text/x-ocaml", "text/x-fsharp", "text/x-dsrc", "text/x-nim",
        "text/x-crystal", "text/x-v", "text/x-odin", "text/x-julia",
        "text/x-r", "text/x-r-source", "text/x-glsl", "text/x-hlsl",
        "text/x-wgsl", "text/x-qml", "text/x-protobuf", "application/graphql",
        "text/x-graphql", "text/org", "text/x-org", "text/x-rst",
        "text/asciidoc", "text/x-asciidoc", "text/vtt", "application/x-subrip",
        "text/x-ssa", "text/x-ass", "text/x-devicetree", "text/prs.lines.tag",
    ),
})

PRIMARY_MIME: frozendict[str, str] = frozendict({
    "file-manager": "inode/directory",
    "browser": "x-scheme-handler/https",
    "text-editor": "text/plain",
})

DESKTOP_CATEGORIES: frozendict[str, str] = frozendict({
    "file-manager": "System;FileManager;",
    "browser": "Network;WebBrowser;",
    "text-editor": "Utility;TextEditor;",
})

LUA_TOKEN = re.compile(
    r"(?P<comment>--(?:\[(?P<ceq>=*)\[.*?\](?P=ceq)\]|[^\n]*))"
    r"|(?P<string>'(?:\\.|[^'\\\r\n])*'|\"(?:\\.|[^\"\\\r\n])*\")"
    r"|(?P<long>\[(?P<seq>=*)\[.*?\](?P=seq)\])"
    r"|(?P<name>[A-Za-z_][A-Za-z_0-9]*)|(?P<symbol>[^\s])",
    re.DOTALL,
)
LONG_BRACKET_RE = re.compile(r"\[=*\[")
BLOCK_COMMENT_RE = re.compile(r"--\[(=*)\[")
LOCAL_PREFIX_RE = re.compile(r"\s*(?:local\s+)?")
SECTION_HEADER_RE = re.compile(r"\[([^\]]+)\]")
CONTINUATION_SYMBOLS: frozenset[str] = frozenset({
    ".", "+", "-", "*", "/", "%", "^", "&", "|", "~", "<", ">", "=", "and", "or", "(", "[", "{",
})


def make_ini_parser(*, preserve_case: bool = False) -> ConfigParser:
    """Create a fault-tolerant ConfigParser that never aborts on duplicate keys or sections."""
    parser = ConfigParser(interpolation=None, strict=False)
    if preserve_case:
        parser.optionxform = str  # type: ignore[assignment]
    return parser


def safe_first_word(command_str: str, fallback: str = "") -> str:
    """Extract the base executable name safely, even if quotes are unbalanced."""
    raw = command_str.strip()
    if not raw:
        return fallback
    try:
        words = shlex.split(raw)
    except ValueError:
        words = raw.split()
    return Path(words[0]).name if words else fallback


def is_socket_file(path: Path) -> bool:
    try:
        return stat.S_ISSOCK(path.stat().st_mode)
    except OSError:
        return False


def discover_desktop_user() -> pwd.struct_passwd | None:
    """Identify the active desktop user across sudo, pkexec, file ownership, and /run/user."""
    for env_var in ("SUDO_USER", "DOAS_USER"):
        if (username := os.environ.get(env_var)) and username != "root":
            try:
                return pwd.getpwnam(username)
            except KeyError:
                pass

    for uid_var in ("PKEXEC_UID", "SUDO_UID", "DOAS_UID"):
        if raw_uid := os.environ.get(uid_var):
            try:
                uid = int(raw_uid)
                if uid >= 1000:
                    return pwd.getpwuid(uid)
            except (ValueError, KeyError):
                pass

    for path_candidate in (Path(__file__).resolve(), Path.cwd()):
        try:
            st_uid = path_candidate.stat().st_uid
            if 1000 <= st_uid < 60000:
                return pwd.getpwuid(st_uid)
        except (OSError, KeyError):
            pass

    run_user = Path("/run/user")
    if run_user.is_dir():
        try:
            for entry in sorted(run_user.iterdir()):
                if not entry.name.isdigit():
                    continue
                uid = int(entry.name)
                if 1000 <= uid < 60000 and ((entry / "hypr").is_dir() or (entry / "bus").exists()):
                    return pwd.getpwuid(uid)
        except (OSError, KeyError):
            pass

    for pw in pwd.getpwall():
        if 1000 <= pw.pw_uid < 60000:
            home = Path(pw.pw_dir)
            if (home / ".config/dusky").is_dir() or (home / ".config/hypr").is_dir():
                return pw

    return None


def ensure_desktop_user(*, strict: bool) -> None:
    """Drop root privileges to the desktop user if invoked from a root pacman hook."""
    if os.geteuid() != 0:
        user_bin = str(Path.home() / ".local/bin")
        current_path = os.environ.get("PATH", "/usr/local/bin:/usr/bin")
        if user_bin not in current_path.split(":"):
            os.environ["PATH"] = f"{user_bin}:{current_path}"
        return

    if strict:
        raise ValueError("Run default_apps as the desktop user, not root")

    pw_entry = discover_desktop_user()
    if pw_entry is None:
        raise ValueError("Running as root and could not determine target desktop user")

    os.initgroups(pw_entry.pw_name, pw_entry.pw_gid)
    os.setgid(pw_entry.pw_gid)
    os.setuid(pw_entry.pw_uid)
    os.environ["HOME"] = pw_entry.pw_dir
    os.environ["USER"] = pw_entry.pw_name
    os.environ["LOGNAME"] = pw_entry.pw_name

    for xdg_var in ("XDG_CONFIG_HOME", "XDG_DATA_HOME", "XDG_CACHE_HOME", "XDG_STATE_HOME"):
        if os.environ.get(xdg_var, "").startswith("/root"):
            os.environ.pop(xdg_var, None)

    user_bin = str(Path(pw_entry.pw_dir) / ".local/bin")
    current_path = os.environ.get("PATH", "/usr/local/bin:/usr/bin")
    if user_bin not in current_path.split(":"):
        os.environ["PATH"] = f"{user_bin}:{current_path}"

    runtime_dir = Path(f"/run/user/{pw_entry.pw_uid}")
    if runtime_dir.is_dir():
        os.environ["XDG_RUNTIME_DIR"] = str(runtime_dir)


def data_roots(data_home: Path) -> tuple[Path, ...]:
    xdg_dirs_raw = os.environ.get("XDG_DATA_DIRS") or "/usr/local/share:/usr/share"
    return (data_home, *(Path(p) for p in xdg_dirs_raw.split(":") if p.strip()))


def build_user_env(config_home: Path, data_home: Path) -> dict[str, str]:
    """Construct a complete user environment, recovering session sockets stripped by sudo/hooks."""
    env = dict(os.environ)
    uid = os.getuid()
    runtime_dir = Path(env.get("XDG_RUNTIME_DIR") or f"/run/user/{uid}")
    if runtime_dir.is_dir():
        env["XDG_RUNTIME_DIR"] = str(runtime_dir)

    env.setdefault("XDG_CONFIG_HOME", str(config_home))
    env.setdefault("XDG_DATA_HOME", str(data_home))
    if not env.get("XDG_CURRENT_DESKTOP"):
        env["XDG_CURRENT_DESKTOP"] = "Hyprland"

    if not env.get("DBUS_SESSION_BUS_ADDRESS"):
        bus_socket = runtime_dir / "bus"
        if is_socket_file(bus_socket):
            env["DBUS_SESSION_BUS_ADDRESS"] = f"unix:path={bus_socket}"

    hypr_dir = runtime_dir / "hypr"
    sig = env.get("HYPRLAND_INSTANCE_SIGNATURE")
    if sig and not is_socket_file(hypr_dir / sig / ".socket.sock"):
        env.pop("HYPRLAND_INSTANCE_SIGNATURE", None)
        sig = None
    if not sig and hypr_dir.is_dir():
        candidates: list[tuple[float, str]] = []
        try:
            for entry in hypr_dir.iterdir():
                sock = entry / ".socket.sock"
                if entry.is_dir() and is_socket_file(sock):
                    candidates.append((sock.stat().st_mtime, entry.name))
        except OSError:
            pass
        if candidates:
            candidates.sort(reverse=True)
            env["HYPRLAND_INSTANCE_SIGNATURE"] = candidates[0][1]

    return env


def tokens(text: str, *, strict: bool = True) -> list[Token]:
    result: list[Token] = []
    for token in LUA_TOKEN.finditer(text):
        value = token.group()
        if token.lastgroup == "symbol" and value in ("'", '"'):
            if strict:
                raise ValueError("Unterminated Lua string")
            continue
        if token.lastgroup == "symbol" and value == "[" and LONG_BRACKET_RE.prefixmatch(text[token.start():]):
            if strict:
                raise ValueError("Unterminated Lua long string")
            continue
        if token.lastgroup == "comment":
            opening = BLOCK_COMMENT_RE.prefixmatch(value)
            if opening and not value.endswith(f"]{opening[1]}]"):
                if strict:
                    raise ValueError("Unterminated Lua block comment")
        else:
            result.append(token)
    return result


def edits(text: str, changes: Sequence[tuple[int, int, str]]) -> str:
    for start, end, value in sorted(changes, reverse=True):
        text = text[:start] + value + text[end:]
    return text


@dataclass(frozen=True, slots=True)
class LuaAssignment:
    line_start: int
    line_end: int
    val_start: int
    val_end: int
    value: str


def find_assignments(text: str, variable: str, *, strict: bool = True) -> list[LuaAssignment]:
    ts = tokens(text, strict=strict)
    found: list[LuaAssignment] = []
    for i, token in enumerate(ts):
        if token.group() != variable or i + 1 >= len(ts) or ts[i + 1].group() != "=":
            continue
        line_start = text.rfind("\n", 0, token.start()) + 1
        prefix = text[line_start : token.start()]
        if not LOCAL_PREFIX_RE.fullmatch(prefix):
            continue
        if i + 2 >= len(ts):
            if strict:
                raise ValueError(f"Missing value for {variable}")
            continue
        val_tok = ts[i + 2]
        if val_tok.lastgroup != "string":
            if strict:
                raise ValueError(f"{variable} must be a quoted application name")
            continue
        if i + 3 < len(ts):
            following = ts[i + 3]
            continuation = (
                following.group() in CONTINUATION_SYMBOLS
                or following.lastgroup in ("string", "long")
            )
            if continuation or ("\n" not in text[val_tok.end() : following.start()] and following.group() != ";"):
                if strict:
                    raise ValueError(f"{variable} uses an expression; select a literal application name first")
                continue
        line_nl = text.find("\n", val_tok.end())
        line_end = len(text) if line_nl == -1 else line_nl + 1
        found.append(LuaAssignment(line_start, line_end, val_tok.start(), val_tok.end(), val_tok.group()[1:-1]))
    return found


def assignment(text: str, variable: str) -> tuple[int, int, str] | None:
    found = find_assignments(text, variable, strict=True)
    if len(found) > 1:
        raise ValueError(f"Multiple assignments to {variable}; keep one active default")
    if not found:
        return None
    item = found[0]
    return (item.val_start, item.val_end, item.value)


def raw_defaults(text: str) -> dict[str, str]:
    """Return the exact literal string assigned to each category variable in Lua."""
    result: dict[str, str] = {}
    for kind, cat in CATEGORIES.items():
        found = find_assignments(text, cat.variable, strict=False)
        result[kind] = found[-1].value if found else "unknown"
    return result


def defaults(text: str) -> dict[str, str]:
    """Return normalized catalog keys (or raw strings for custom commands) per category."""
    result: dict[str, str] = {}
    for kind, cat in CATEGORIES.items():
        found = find_assignments(text, cat.variable, strict=False)
        command = found[-1].value if found else "unknown"
        app = cat.find_app(command)
        result[kind] = app.key if app else command
    return result


def set_default(text: str, cat: Category, key: str, *, strict: bool = True) -> str:
    if strict:
        value = assignment(text, cat.variable)
        if value:
            return edits(text, [(value[0], value[1], f'"{key}"')])
    else:
        found = find_assignments(text, cat.variable, strict=False)
        if found:
            first = found[0]
            patch_list: list[tuple[int, int, str]] = [(first.val_start, first.val_end, f'"{key}"')]
            for extra in found[1:]:
                patch_list.append((extra.line_start, extra.line_end, ""))
            return edits(text, patch_list)
    stripped = text.rstrip("\n")
    return f'{stripped}\n{cat.variable} = "{key}"\n' if stripped else f'{cat.variable} = "{key}"\n'


def closing(ts: Sequence[Token], start: int) -> int:
    pairs = {"(": ")", "{": "}", "[": "]"}
    stack: list[str] = []
    for i in range(start, len(ts)):
        value = ts[i].group()
        if value in pairs:
            stack.append(pairs[value])
        elif value in pairs.values():
            if not stack or value != stack.pop():
                raise ValueError("Unbalanced Lua delimiters in keybinds.lua")
            if not stack:
                return i
    raise ValueError("Unclosed Lua call in keybinds.lua")


def sequence(ts: Sequence[Token], i: int, values: tuple[str, ...]) -> bool:
    return tuple(t.group() for t in ts[i : i + len(values)]) == values


def launch_expression(kind: str, app: App, terminal: str, *, strict: bool = True) -> str:
    variable = CATEGORIES[kind].variable
    if not app.terminal:
        return f'"dusky-run " .. {variable}'
    # Existing manual terminal options remain in the Lua variable; use only
    # the executable name to choose its command/application-ID syntax.
    term_bin = safe_first_word(terminal, fallback="kitty")
    editor = kind == "text-editor"
    # Keep editor app IDs used by existing Hyprland window rules.
    # Ghostty uses GTK4 GApplication IDs (requiring a period), so it must use '-e'.
    match term_bin:
        case "kitty":
            suffix = f'" --app-id " .. {variable} .. " "' if editor else '" "'
        case "foot":
            suffix = f'" --app-id=" .. {variable} .. " "' if editor else '" "'
        case "alacritty":
            suffix = f'" --class " .. {variable} .. " -e "' if editor else '" -e "'
        case "wezterm":
            suffix = f'" start --class " .. {variable} .. " -- "' if editor else '" start -- "'
        case "ghostty" | "konsole":
            suffix = '" -e "'
        case "gnome-terminal":
            suffix = '" -- "'
        case _:
            if strict:
                raise ValueError(f"Unsupported terminal for {app.key}: {term_bin!r}")
            suffix = '" -e "'
    return f'"dusky-run " .. terminal .. {suffix} .. {variable}'


def set_binding(text: str, cat: Category, expression: str, *, strict: bool = True) -> str:
    ts = tokens(text, strict=strict)
    changes: list[tuple[int, int, str]] = []
    found = False
    i = 0
    while i < len(ts):
        if not sequence(ts, i, ("hl", ".", "bind", "(")):
            i += 1
            continue
        try:
            end = closing(ts, i + 3)
        except ValueError:
            if strict:
                raise
            i += 4
            continue
        descriptions = [
            j
            for j in range(i + 4, end - 2)
            if sequence(ts, j, ("description", "="))
            and ts[j + 2].lastgroup == "string"
            and ts[j + 2].group()[1:-1] == cat.description
        ]
        if descriptions:
            calls = [
                j
                for j in range(i + 4, end)
                if sequence(ts, j, ("hl", ".", "dsp", ".", "exec_cmd", "("))
            ]
            if len(calls) != 1 or len(descriptions) != 1:
                if strict:
                    raise ValueError(f"Cannot locate one exec_cmd for {cat.description!r}")
                i = end + 1
                continue
            call = calls[0] + 5
            try:
                call_end = closing(ts, call)
                tables = [(j, closing(ts, j)) for j in range(i + 4, end) if ts[j].group() == "{"]
            except ValueError:
                if strict:
                    raise
                i = end + 1
                continue
            tables = [(a, b) for a, b in tables if a < descriptions[0] < b]
            if not tables:
                if strict:
                    raise ValueError(f"Missing options table for {cat.description!r}")
                i = end + 1
                continue
            found = True
            changes.append((ts[call].end(), ts[call_end].start(), expression))
            a, b = max(tables)
            if not any(sequence(ts, j, ("submap_universal", "=")) for j in range(a + 1, b)):
                separator = "" if ts[b - 1].group() in (",", ";") else ","
                changes.append((ts[b - 1].end(), ts[b - 1].end(), separator + " submap_universal = true"))
        i = end + 1
    if found:
        return edits(text, changes)
    stripped = text.rstrip("\n")
    prefix = f"{stripped}\n\n" if stripped else ""
    return (
        f"{prefix}-- Auto-generated by Dusky Default Apps\nhl.bind(\n"
        f'    "{cat.binding}",\n    hl.dsp.exec_cmd({expression}),\n'
        f'    {{ description = "{cat.description}", submap_universal = true }}\n)\n'
    )


def writable_target(path: Path) -> Path:
    """Resolve symlinks only if the target resides in a user-writable directory."""
    abs_path = Path(os.path.abspath(path))
    if not abs_path.is_symlink():
        return abs_path
    try:
        resolved = abs_path.resolve(strict=True)
        if os.access(resolved.parent, os.W_OK):
            return resolved
    except OSError:
        pass
    return abs_path


def atomic_write(path: Path, data: bytes) -> None:
    """Replace a file durably, retaining its mode/owner and writable symlink target."""
    target = writable_target(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    try:
        original = target.stat() if not target.is_symlink() and target.exists() else None
    except OSError:
        original = None
    fd, name = tempfile.mkstemp(prefix=".default-apps-", dir=target.parent)
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(data)
            stream.flush()
            if original is not None:
                current = os.fstat(stream.fileno())
                if (current.st_uid, current.st_gid) != (original.st_uid, original.st_gid):
                    try:
                        os.fchown(stream.fileno(), original.st_uid, original.st_gid)
                    except PermissionError:
                        pass
                os.fchmod(stream.fileno(), stat.S_IMODE(original.st_mode))
            else:
                os.fchmod(stream.fileno(), DEFAULT_FILE_MODE)
            os.fsync(stream.fileno())
        os.replace(name, target)
        directory = os.open(target.parent, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    finally:
        Path(name).unlink(missing_ok=True)


def write_batch(changes: Mapping[Path, bytes]) -> None:
    """Atomically write modified files and roll back on an I/O failure or signal."""
    targets = {p: writable_target(p) for p in changes}
    originals: dict[Path, bytes | None] = {}
    for p, target in targets.items():
        try:
            originals[p] = target.read_bytes() if not target.is_symlink() and target.is_file() else None
        except OSError:
            originals[p] = None
    written: list[Path] = []
    try:
        for path, content in changes.items():
            if originals[path] != content:
                written.append(path)
                atomic_write(targets[path], content)
    except BaseException as error:
        failures: list[str] = []
        for path in reversed(written):
            target = targets[path]
            try:
                if originals[path] is None:
                    target.unlink(missing_ok=True)
                else:
                    atomic_write(target, originals[path])
            except OSError as rollback_error:
                failures.append(f"{target}: {rollback_error}")
        if failures:
            raise OSError("Write failed and rollback was incomplete: " + "; ".join(failures)) from error
        raise


@contextmanager
def config_lock(settings: Path, *, timeout: float = 5.0) -> Iterator[None]:
    settings.mkdir(parents=True, exist_ok=True)
    lock_file = settings / ".default_apps.lock"
    with lock_file.open("a", encoding="utf-8") as stream:
        deadline = time.monotonic() + timeout
        while True:
            try:
                fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except BlockingIOError:
                if time.monotonic() >= deadline:
                    raise ValueError("Default app configuration is busy; try again") from None
                time.sleep(0.05)
        try:
            yield
        finally:
            try:
                fcntl.flock(stream, fcntl.LOCK_UN)
            except OSError:
                pass


def run_command(
    argv: Sequence[str],
    *,
    env: Mapping[str, str] | None = None,
    timeout: float = 10.0,
) -> str:
    command = shutil.which(argv[0], path=(env or os.environ).get("PATH"))
    if command is None:
        raise OSError(f"{argv[0]} is unavailable")
    try:
        result = subprocess.run(
            [command, *argv[1:]],
            stdin=subprocess.DEVNULL,
            capture_output=True,
            encoding="utf-8",
            errors="replace",
            env=env,
            timeout=timeout,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as error:
        raise OSError(f"{argv[0]}: {error}") from error
    if result.returncode != 0:
        detail = result.stderr.strip() or result.stdout.strip() or f"exit status {result.returncode}"
        raise OSError(f"{argv[0]} exited {result.returncode}: {detail}")
    return result.stdout


def desktop_file(app: App, data_home: Path) -> Path | None:
    """Return the highest-precedence .desktop entry across XDG data roots."""
    return next(
        (
            root / "applications" / app.desktop
            for root in data_roots(data_home)
            if (root / "applications" / app.desktop).is_file()
        ),
        None,
    )


def validate_desktop_path(path: Path) -> None:
    """Validate that a .desktop file is well-formed, non-hidden, and its TryExec exists."""
    entry = make_ini_parser()
    try:
        entry.read(path, encoding="utf-8")
        desktop = entry["Desktop Entry"]
        if desktop.getboolean("Hidden", fallback=False):
            raise ValueError(f"Desktop entry is hidden: {path}")
        if (try_exec := desktop.get("TryExec")) and not shutil.which(try_exec):
            raise ValueError(f"Desktop entry requires an unavailable command ({try_exec}): {path}")
    except (ConfigError, KeyError, ValueError) as error:
        if isinstance(error, ValueError) and str(error).startswith("Desktop entry"):
            raise
        raise ValueError(f"Invalid desktop entry {path}: {error}") from error


def mime_defaults(text: str, updates: Mapping[str, str], *, strict: bool = True) -> str:
    """Update [Default Applications], preserving comments/groups and deduplicating keys."""
    if not updates:
        return text
    parser = make_ini_parser(preserve_case=True)
    try:
        parser.read_string(text)
    except ConfigError as error:
        if strict:
            raise ValueError(f"Invalid MIME configuration: {error}") from error
    pending = dict(updates)
    seen_updated: set[str] = set()
    output: list[str] = []
    section = ""
    found = False
    for line in text.splitlines(keepends=True):
        stripped = line.strip()
        header = SECTION_HEADER_RE.prefixmatch(stripped)
        if header:
            if section == "Default Applications" and pending:
                output.extend(f"{key}={value}\n" for key, value in pending.items())
                seen_updated.update(pending)
                pending.clear()
            section = header[1].strip()
            found |= section == "Default Applications"
        elif section == "Default Applications" and not stripped.startswith(("#", ";")) and "=" in line:
            key = line.split("=", 1)[0].strip()
            if key in updates:
                if key in seen_updated:
                    continue
                line = f"{key}={updates[key]}\n"
                seen_updated.add(key)
                pending.pop(key, None)
        output.append(line if line.endswith("\n") else line + "\n")
    if not found:
        if output and not output[-1].endswith("\n\n"):
            output.append("\n")
        output.append("[Default Applications]\n")
    output.extend(f"{key}={value}\n" for key, value in pending.items())
    return "".join(output)


def terminal_desktop(kind: str, app: App, fallback_terminal: str = "kitty") -> bytes:
    """Use the selected default terminal for file/URI launches as well as keys."""
    argument = "%U" if kind == "browser" else "%F"
    if shutil.which("xdg-terminal-exec"):
        exec_line = f"xdg-terminal-exec --app-id={app.key} -- {app.command} {argument}"
    else:
        term_bin = safe_first_word(fallback_terminal, fallback="kitty")
        exec_line = f"{term_bin} -e {app.command} {argument}"
    return (
        "[Desktop Entry]\n"
        "Type=Application\n"
        f"Name={app.label}\n"
        f"Icon={app.key}\n"
        f"TryExec={app.command}\n"
        f"Exec={exec_line}\n"
        "Terminal=false\n"
        f"Categories={DESKTOP_CATEGORIES[kind]}\n"
        f"MimeType={';'.join(MIMES[kind])};\n"
    ).encode()


def file_manager_service(app: App, data_home: Path) -> bytes | None:
    """Find the installed GUI file manager's native D-Bus FileManager1 activation command."""
    destination_abs = Path(os.path.abspath(data_home / "dbus-1/services/org.freedesktop.FileManager1.service"))
    commands = {name.casefold() for name in (app.key, *app.commands)}
    for root in data_roots(data_home):
        service_dir = root / "dbus-1/services"
        if not service_dir.is_dir():
            continue
        for path in sorted(service_dir.glob("*.service")):
            if Path(os.path.abspath(path)) == destination_abs:
                continue  # Never mistake our generated user service for a vendor provider.
            parser = make_ini_parser()
            try:
                parser.read(path, encoding="utf-8")
            except (ConfigError, OSError):
                continue
            if not parser.has_section("D-BUS Service"):
                continue
            service = parser["D-BUS Service"]
            if service.get("Name") != "org.freedesktop.FileManager1":
                continue
            exec_raw = service.get("Exec", "").strip()
            if not exec_raw:
                continue
            exe_name = safe_first_word(exec_raw, fallback="").casefold()
            if exe_name in commands:
                return (
                    "[D-BUS Service]\n"
                    "Name=org.freedesktop.FileManager1\n"
                    f"Exec={exec_raw}\n"
                ).encode()
    return None


class Switcher:
    def __init__(self, home: Path | None = None) -> None:
        base_home = home or Path.home()
        self.config = Path(os.environ.get("XDG_CONFIG_HOME") or base_home / ".config")
        self.variables = self.config / "hypr/edit_here/source/default_apps.lua"
        self.bindings = self.config / "hypr/edit_here/source/keybinds.lua"
        self.settings = self.config / "dusky/settings"
        self.data_home = Path(os.environ.get("XDG_DATA_HOME") or base_home / ".local/share")

    def _read_text_safe(self, path: Path) -> str:
        try:
            return path.read_text(encoding="utf-8") if path.is_file() else ""
        except OSError:
            return ""

    def current(self) -> dict[str, str]:
        return defaults(self._read_text_safe(self.variables))

    def saved(self, kind: str, *, strict: bool = True) -> str | None:
        cat = CATEGORIES[kind]
        state = self.settings / cat.state
        smart = state.with_name(f"{state.name}.smart")
        if smart.is_file():
            try:
                key = smart.read_text(encoding="utf-8").strip()
            except OSError as error:
                if strict:
                    raise
                return None
            if app := cat.find_app(key):
                return app.key
            if strict:
                raise ValueError(f"Unknown saved {cat.description.lower()} choice in {smart}: {key!r}")
            # Reject invalid saved choices rather than falling through and overwriting .smart
            return None
        if state.is_file():
            try:
                value = state.read_text(encoding="utf-8").strip()
            except OSError as error:
                if strict:
                    raise
                return None
            lowered = value.casefold()
            if lowered in ("true", "false"):
                return cat.legacy[lowered == "false"]
            if app := cat.find_app(value):
                return app.key
            if strict:
                raise ValueError(f"Invalid legacy state in {state}: {value!r}")
        return None

    def _validate_app(self, app: App) -> None:
        if not app.is_installed:
            raise ValueError(f"{app.label} is not installed ({app.command})")
        if not app.terminal:
            path = desktop_file(app, self.data_home)
            if path is None:
                raise ValueError(f"Desktop entry is unavailable: {app.desktop}")
            validate_desktop_path(path)

    def apply(
        self,
        requested: Mapping[str, str] | None,
        kinds: Sequence[str],
        *,
        strict: bool = True,
    ) -> list[str]:
        try:
            with config_lock(self.settings):
                return self._apply_locked(requested, kinds, strict=strict)
        except (OSError, ValueError) as error:
            if strict:
                raise
            return [f"Warning: {error}"]

    def _apply_locked(
        self,
        requested: Mapping[str, str] | None,
        kinds: Sequence[str],
        *,
        strict: bool,
    ) -> list[str]:
        warnings: list[str] = []

        def warn_or_raise(msg: str, exc: Exception | None = None) -> None:
            if strict:
                if exc is not None:
                    raise ValueError(msg) from exc
                raise ValueError(msg)
            warnings.append(f"Warning: {msg}")

        variables_text = self._read_text_safe(self.variables)
        bindings_text = self._read_text_safe(self.bindings)
        previous_raw = raw_defaults(variables_text)
        previous = defaults(variables_text)

        # Always evaluate 'terminal' first when restoring multiple categories.
        ordered_kinds = sorted(kinds, key=lambda k: 0 if k == "terminal" else 1)
        effective_requests: dict[str, str] = {}
        persist_state_kinds: set[str] = set()

        if requested is not None:
            effective_requests.update(requested)
            persist_state_kinds.update(requested)
        else:
            for kind in ordered_kinds:
                cat = CATEGORIES[kind]
                state = self.settings / cat.state
                smart = state.with_name(f"{state.name}.smart")
                had_smart = smart.is_file()
                try:
                    saved_key = self.saved(kind, strict=strict)
                except ValueError as error:
                    warn_or_raise(str(error), error)
                    saved_key = None
                if saved_key is not None:
                    effective_requests[kind] = saved_key
                    if not had_smart:
                        persist_state_kinds.add(kind)
            # A fresh installation has no saved state yet; its configured file manager
            # still needs native D-Bus FileManager1 activation configured.
            if "file-manager" in ordered_kinds and "file-manager" not in effective_requests:
                current_fm = previous.get("file-manager", "unknown")
                if CATEGORIES["file-manager"].find_app(current_fm):
                    effective_requests["file-manager"] = current_fm

        if not effective_requests:
            return warnings or ["No saved choices found; nothing changed."]

        selected: dict[str, App] = {}
        for kind in ordered_kinds:
            if kind not in effective_requests:
                continue
            key = effective_requests[kind]
            try:
                app = CATEGORIES[kind].app(key)
                self._validate_app(app)
                selected[kind] = app
            except ValueError as error:
                warn_or_raise(str(error), error)

        if not selected:
            return warnings or ["No valid installed applications to apply."]

        for kind, app in selected.items():
            # Preserve manual arguments in default_apps.lua if the same binary is already configured
            existing_raw = previous_raw.get(kind, "unknown")
            if (
                requested is None
                and existing_raw != "unknown"
                and safe_first_word(existing_raw) in app.candidates
            ):
                target_cmd = existing_raw
            else:
                target_cmd = app.command
            try:
                variables_text = set_default(
                    variables_text, CATEGORIES[kind], target_cmd, strict=strict
                )
            except ValueError as error:
                warn_or_raise(f"Could not update {CATEGORIES[kind].variable} in default_apps.lua: {error}", error)

        current_raw = raw_defaults(variables_text)
        current = defaults(variables_text)
        active_terminal = current_raw.get("terminal", "kitty")
        if active_terminal == "unknown":
            active_terminal = selected["terminal"].command if "terminal" in selected else "kitty"

        binding_apps = dict(selected)
        if "terminal" in selected:
            # Refresh dependent TUI commands using the final terminal without altering their choice.
            for kind in ("file-manager", "browser", "text-editor"):
                if kind not in selected:
                    app_candidate = CATEGORIES[kind].find_app(current.get(kind, ""))
                    if app_candidate and app_candidate.terminal:
                        binding_apps[kind] = app_candidate

        for kind, app in binding_apps.items():
            try:
                expression = launch_expression(kind, app, active_terminal, strict=strict)
                bindings_text = set_binding(bindings_text, CATEGORIES[kind], expression, strict=strict)
            except ValueError as error:
                warn_or_raise(f"Could not update keybind for {CATEGORIES[kind].description}: {error}", error)

        changes: dict[Path, bytes] = {}
        for kind, app in selected.items():
            if kind in persist_state_kinds:
                cat = CATEGORIES[kind]
                state = self.settings / cat.state
                changes[state.with_name(f"{state.name}.smart")] = f"{app.key}\n".encode()
                legacy_flag = app.terminal if kind in ("file-manager", "text-editor") else app.key == cat.legacy[0]
                changes[state] = f"{str(legacy_flag).lower()}\n".encode()

        if variables_text or self.variables.exists():
            changes[self.variables] = variables_text.encode()
        if bindings_text or self.bindings.exists():
            changes[self.bindings] = bindings_text.encode()

        desktop_names = tuple(
            name.lower()
            for name in os.environ.get("XDG_CURRENT_DESKTOP", "Hyprland").split(":")
            if name.strip()
        )
        updates = {
            mime: app.desktop
            for kind, app in selected.items()
            for mime in MIMES.get(kind, ())
        }
        if updates:
            mime_path = self.config / "mimeapps.list"
            mime_text = self._read_text_safe(mime_path)
            try:
                changes[mime_path] = mime_defaults(mime_text, updates, strict=strict).encode()
            except ValueError as error:
                warn_or_raise(str(error), error)

            # Desktop-specific user overrides take precedence over mimeapps.list.
            for name in desktop_names:
                dt_mime_path = self.config / f"{name}-mimeapps.list"
                if dt_mime_path.is_file():
                    dt_text = self._read_text_safe(dt_mime_path)
                    parser = make_ini_parser(preserve_case=True)
                    try:
                        parser.read_string(dt_text)
                        existing = (
                            parser["Default Applications"]
                            if parser.has_section("Default Applications")
                            else {}
                        )
                        overrides = {mime: val for mime, val in updates.items() if mime in existing}
                        if overrides:
                            changes[dt_mime_path] = mime_defaults(dt_text, overrides, strict=strict).encode()
                    except (ConfigError, ValueError) as error:
                        warn_or_raise(f"Invalid MIME configuration in {dt_mime_path}: {error}", error)

        terminal_apps = {kind: app for kind, app in binding_apps.items() if app.terminal}
        configure_terminal = "terminal" in selected or bool(terminal_apps)
        if configure_terminal and strict:
            required = ("xdg-terminal-exec", "update-desktop-database") if terminal_apps else ("xdg-terminal-exec",)
            for req_cmd in required:
                if not shutil.which(req_cmd):
                    raise ValueError(f"{req_cmd} is required for terminal configuration")

        for kind, app in terminal_apps.items():
            changes[self.data_home / "applications" / app.desktop] = terminal_desktop(
                kind, app, fallback_terminal=active_terminal
            )

        terminal_app: App | None = None
        if configure_terminal:
            term_cmd_name = safe_first_word(active_terminal, fallback="kitty")
            terminal_app = CATEGORIES["terminal"].find_app(term_cmd_name)
            if terminal_app is None and strict:
                raise ValueError(f"Unknown terminal choice: {term_cmd_name!r}")
            if terminal_app is not None:
                term_list_paths = [self.config / "xdg-terminals.list"]
                term_list_paths.extend(
                    self.config / f"{name}-xdg-terminals.list"
                    for name in desktop_names
                    if (self.config / f"{name}-xdg-terminals.list").is_file()
                )
                for path in term_list_paths:
                    lines = self._read_text_safe(path).splitlines()
                    filtered = [
                        line
                        for line in lines
                        if line.strip().lstrip("+-").partition(":")[0] != terminal_app.desktop
                    ]
                    body = "\n".join(filtered).rstrip("\n")
                    content = f"{terminal_app.desktop}\n{body}\n" if body else f"{terminal_app.desktop}\n"
                    changes[path] = content.encode()

        provider: bytes | None = None
        notes: list[str] = []
        if "file-manager" in selected:
            fm_app = selected["file-manager"]
            destination = self.data_home / "dbus-1/services/org.freedesktop.FileManager1.service"
            if fm_app.terminal:
                # The activation entry remembers the last GUI; selecting a terminal manager must not overwrite it.
                if not destination.is_file():
                    prior_app = next(
                        (
                            candidate
                            for candidate in CATEGORIES["file-manager"].apps
                            if not candidate.terminal and candidate.key == previous.get("file-manager")
                        ),
                        None,
                    )
                    if prior_app is not None:
                        provider = file_manager_service(prior_app, self.data_home)
                notes.append("Show in Folder retains the existing GUI provider.")
            else:
                provider = file_manager_service(fm_app, self.data_home)
                if provider is None:
                    notes.append(
                        f"{fm_app.label} has no installed FileManager1 provider; Show in Folder retains its existing GUI provider."
                    )
            if provider is not None:
                changes[destination] = provider
                notes.append(
                    "Native Show in Folder activation saved. Log out and back in if the previous GUI still opens."
                )

        write_batch(changes)

        user_env = build_user_env(self.config, self.data_home)
        post_failures: list[str] = []

        if configure_terminal:
            try:
                cache_dir = Path(user_env.get("XDG_CACHE_HOME") or Path.home() / ".cache")
                cache_entry = cache_dir / "xdg-terminal-exec"
                if cache_entry.is_dir() and not cache_entry.is_symlink():
                    shutil.rmtree(cache_entry, ignore_errors=True)
                else:
                    cache_entry.unlink(missing_ok=True)
            except OSError as error:
                post_failures.append(str(error))

        if terminal_apps and shutil.which("update-desktop-database"):
            try:
                run_command(
                    ["update-desktop-database", str(self.data_home / "applications")],
                    env=user_env,
                )
            except OSError as error:
                post_failures.append(str(error))

        if configure_terminal and terminal_app is not None and shutil.which("xdg-terminal-exec"):
            try:
                actual = run_command(["xdg-terminal-exec", "--print-id"], env=user_env).strip()
                if actual != terminal_app.desktop:
                    post_failures.append(
                        f"Default terminal: expected {terminal_app.desktop}, got {actual or 'no terminal'}; check xdg-terminals.list directives and terminal desktop entries"
                    )
            except OSError as error:
                post_failures.append(str(error))

        if shutil.which("xdg-mime"):
            for kind, app in selected.items():
                if kind in MIMES:
                    try:
                        mime = PRIMARY_MIME[kind]
                        actual = run_command(["xdg-mime", "query", "default", mime], env=user_env).strip()
                        if actual != app.desktop:
                            post_failures.append(
                                f"{mime}: expected {app.desktop}, got {actual or 'no handler'}; check installed desktop entries and MIME overrides"
                            )
                    except OSError as error:
                        post_failures.append(str(error))

        if provider is not None and user_env.get("DBUS_SESSION_BUS_ADDRESS") and shutil.which("busctl"):
            try:
                run_command(
                    [
                        "busctl", "--user", "call", "org.freedesktop.DBus",
                        "/org/freedesktop/DBus", "org.freedesktop.DBus", "ReloadConfig",
                    ],
                    env=user_env,
                    timeout=5.0,
                )
            except OSError as error:
                post_failures.append(str(error))

        if user_env.get("HYPRLAND_INSTANCE_SIGNATURE") and shutil.which("hyprctl"):
            try:
                run_command(["hyprctl", "reload", "config-only"], env=user_env, timeout=5.0)
                errors = run_command(["hyprctl", "configerrors"], env=user_env, timeout=5.0).strip()
                if errors:
                    post_failures.append(f"Hyprland configuration errors: {errors}")
            except OSError as error:
                post_failures.append(str(error))

        if post_failures:
            warn_or_raise("Choices saved, but application is incomplete: " + "; ".join(post_failures))

        summary = [f"{CATEGORIES[kind].description}: {app.label}" for kind, app in selected.items()]
        return summary + notes + warnings


def menu(switcher: Switcher, category: str | None) -> tuple[int, str]:
    import curses
    import termios

    if not sys.stdin.isatty() or not sys.stdout.isatty():
        raise ValueError("Menus require a terminal; use --set NAME or --apply-state")

    stdin_fd = sys.stdin.fileno()
    saved_termios = termios.tcgetattr(stdin_fd)

    def interact(screen: curses.window) -> tuple[int, str]:
        mode = termios.tcgetattr(stdin_fd)
        mode[0] &= ~termios.IXON
        termios.tcsetattr(stdin_fd, termios.TCSANOW, mode)
        try:
            curses.curs_set(0)
        except curses.error:
            pass
        curses.set_escdelay(80)
        curses.mousemask(curses.ALL_MOUSE_EVENTS)
        screen.keypad(True)
        normal = accent = success = error_style = 0
        if curses.has_colors():
            curses.assume_default_colors(-1, -1)
            for pair, foreground in enumerate((-1, curses.COLOR_BLUE, curses.COLOR_GREEN, curses.COLOR_RED), 1):
                curses.init_pair(pair, foreground, -1)
            normal, accent, success, error_style = (curses.color_pair(i) for i in range(1, 5))
        muted = normal | curses.A_DIM
        screen.bkgd(" ", normal)
        selected = 0
        offset = 0
        kind = category
        status = ""
        outcome = (0, "")
        current = switcher.current()
        installed_cache = {
            app.key: app.is_installed
            for cat in CATEGORIES.values()
            for app in cat.apps
        }
        if kind:
            selected = next(
                (i for i, app in enumerate(CATEGORIES[kind].apps) if app.key == current[kind]),
                0,
            )
        while True:
            screen.erase()
            height, width = screen.getmaxyx()
            small = height < 14 or width < 40
            left, right = 2, width - 3
            content_width = max(0, right - left + 1)

            def put(row: int, text: str, attr: int = normal, column: int = left) -> None:
                if 0 <= row < height - 1 and 0 <= column < width - 1:
                    try:
                        screen.addnstr(row, column, text, max(0, width - column - 1), attr)
                    except curses.error:
                        pass

            choices: Sequence[str | App] = list(CATEGORIES) if kind is None else CATEGORIES[kind].apps
            # Use all available vertical rows between row 6 and height - 5 without an artificial 10-item cap.
            visible = max(1, height - 11)
            offset = max(0, min(offset, selected, max(0, len(choices) - visible)))
            if selected >= offset + visible:
                offset = selected - visible + 1
            if small:
                put(0, "Resize to at least 40 columns x 14 rows.", column=0)
                put(1, "q: quit", column=0)
            else:
                title = (
                    "Default Applications"
                    if kind is None
                    else CATEGORIES[kind].description.removeprefix("Launch ").removeprefix("Open ")
                )
                put(1, "DUSKY", accent | curses.A_BOLD)
                eyebrow = "DEFAULT APPLICATIONS"
                put(1, eyebrow, muted, right - len(eyebrow) + 1)
                counter = f"{selected + 1} / {len(choices)}"
                put(3, title, normal | curses.A_BOLD)
                put(3, counter, muted, right - len(counter) + 1)
                if kind:
                    put(4, f"Active: {current[kind]}   |   Terminal: {current['terminal']}", muted)
                else:
                    put(4, "Choose a category to change its default.", muted)
                put(5, "─" * content_width, muted)
                for row, i in enumerate(range(offset, min(offset + visible, len(choices))), 6):
                    choice = choices[i]
                    if isinstance(choice, str):
                        active = False
                        is_avail = True
                        label = CATEGORIES[choice].description.removeprefix("Launch ").removeprefix("Open ")
                        detail = current[choice]
                    else:
                        active = bool(kind and choice.key == current[kind])
                        is_avail = installed_cache.get(choice.key, True)
                        label = choice.label.partition(" (")[0]
                        app_type = "" if kind == "terminal" else ("TUI" if choice.terminal else "GUI")
                        state_tag = "ACTIVE" if active else ("" if is_avail else "MISSING")
                        detail = " · ".join(part for part in (app_type, state_tag) if part)
                    detail = detail[: max(0, content_width // 2 - 2)]
                    highlighted = i == selected
                    base_row_style = normal if is_avail else muted
                    style = accent | curses.A_REVERSE | curses.A_BOLD if highlighted else base_row_style
                    put(row, " " * content_width, style)
                    label_width = max(1, content_width - len(detail) - 6)
                    label = label if len(label) <= label_width else label[: label_width - 1] + "…"
                    put(row, ("› " if highlighted else "  ") + label, style, left + 1)
                    if detail:
                        detail_style = style if highlighted else (success if active else muted)
                        put(row, detail, detail_style, right - len(detail))
                put(height - 5, "─" * content_width, muted)
                message = status or (
                    "Choose an application to make it your default."
                    if kind
                    else "Selections are saved and restored after updates."
                )
                put(height - 4, message, (error_style if outcome[0] else success) if status else muted)
                put(height - 3, "↑↓ / j k  Move    Enter  " + ("Open" if kind is None else "Apply"), muted)
                put(height - 2, "Esc  Back    q  Quit    Click right to apply", muted)
            screen.refresh()
            key = screen.getch()
            if key == -1:
                raise ValueError("Terminal input closed")
            if key in (ord("q"), ord("Q")):
                return outcome
            if key in (27, curses.KEY_BACKSPACE, 127, 8, curses.KEY_LEFT, ord("h"), ord("H")):
                if key == 27 and (kind is None or category is not None):
                    return outcome
                if kind is not None and category is None:
                    prev_kind = kind
                    kind = None
                    selected = list(CATEGORIES).index(prev_kind)
                    offset = 0
                    status = ""
                continue
            if small or key == curses.KEY_RESIZE:
                continue
            apply_choice = key in (10, 13, curses.KEY_ENTER) or (
                kind is None and key in (curses.KEY_RIGHT, ord("l"), ord("L"))
            )
            if key == curses.KEY_MOUSE:
                try:
                    _, x, y, _, button = curses.getmouse()
                except curses.error:
                    continue
                if button & curses.BUTTON4_PRESSED:
                    selected = (selected - 1) % len(choices)
                elif button & getattr(curses, "BUTTON5_PRESSED", 0):
                    selected = (selected + 1) % len(choices)
                elif button & (curses.BUTTON1_PRESSED | curses.BUTTON1_CLICKED | curses.BUTTON1_DOUBLE_CLICKED):
                    clicked = offset + y - 6
                    if 6 <= y < 6 + visible and clicked < len(choices):
                        selected = clicked
                        apply_choice = x >= min(38, width - 10) or bool(button & curses.BUTTON1_DOUBLE_CLICKED)
            elif key in (curses.KEY_UP, ord("k"), ord("K")):
                selected = (selected - 1) % len(choices)
            elif key in (curses.KEY_DOWN, ord("j"), ord("J")):
                selected = (selected + 1) % len(choices)
            elif key == curses.KEY_PPAGE:
                selected = max(0, selected - visible)
            elif key == curses.KEY_NPAGE:
                selected = min(len(choices) - 1, selected + visible)
            elif key in (curses.KEY_HOME, ord("g")):
                selected = 0
            elif key in (curses.KEY_END, ord("G")):
                selected = len(choices) - 1
            if apply_choice:
                if kind is None:
                    chosen_cat = choices[selected]
                    assert isinstance(chosen_cat, str)
                    kind = chosen_cat
                    selected = next(
                        (i for i, app in enumerate(CATEGORIES[kind].apps) if app.key == current[kind]),
                        0,
                    )
                    offset = 0
                    status = ""
                else:
                    chosen_app = choices[selected]
                    assert isinstance(chosen_app, App)
                    try:
                        status = "; ".join(switcher.apply({kind: chosen_app.key}, [kind], strict=True))
                        outcome = (0, status)
                    except (OSError, ValueError) as error:
                        status = f"Error: {error}"
                        outcome = (1, status)
                    current = switcher.current()
                    installed_cache[chosen_app.key] = chosen_app.is_installed

    try:
        return curses.wrapper(interact)
    except (curses.error, termios.error) as error:
        raise ValueError(f"Cannot use this terminal: {error}") from error
    finally:
        try:
            termios.tcsetattr(stdin_fd, termios.TCSANOW, saved_termios)
        except termios.error:
            pass


def parse_args(argv: Sequence[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
        allow_abbrev=False,
    )
    group = parser.add_mutually_exclusive_group()
    for kind in CATEGORIES:
        aliases = [f"--{kind}"]
        if kind == "text-editor":
            aliases.append("--editor")
        group.add_argument(*aliases, dest="category", action="store_const", const=kind)
    actions = parser.add_mutually_exclusive_group()
    actions.add_argument("--set", metavar="NAME", help="Set a catalog choice in the selected category")
    actions.add_argument(
        "--apply-state",
        "--auto",
        action="store_true",
        help="Restore saved choices and native activation for the current file manager without a menu",
    )
    args = parser.parse_args(argv)
    if args.set and not args.category:
        parser.error("--set requires a category flag")
    return args


def interrupted(signum: int, _frame: object) -> None:
    raise SystemExit(128 + signum)


def main(argv: Sequence[str] | None = None) -> int:
    arguments = sys.argv[1:] if argv is None else argv
    is_auto = any(flag in arguments for flag in ("--apply-state", "--auto"))
    try:
        args = parse_args(arguments)
        is_auto = bool(args.apply_state)
        for signum in (signal.SIGHUP, signal.SIGTERM):
            signal.signal(signum, interrupted)
        ensure_desktop_user(strict=not is_auto)
        switcher = Switcher()
        if args.apply_state or args.set:
            kinds = [args.category] if args.category else list(CATEGORIES)
            requested = {args.category: args.set} if args.set else None
            for message in switcher.apply(requested, kinds, strict=not is_auto):
                stream = sys.stderr if message.startswith("Warning:") else sys.stdout
                print(message, file=stream)
            return 0
        code, message = menu(switcher, args.category)
        if message:
            print(message, file=sys.stderr if code else sys.stdout)
        return code
    except (OSError, ValueError) as error:
        if is_auto:
            print(f"Warning: {error}", file=sys.stderr)
            return 0
        print(f"Error: {error}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        return 0 if is_auto else 130
    except BaseException as error:
        if is_auto:
            if not isinstance(error, SystemExit) or error.code not in (0, None):
                print(f"Warning: {error}", file=sys.stderr)
            return 0
        raise


if __name__ == "__main__":
    sys.exit(main())
