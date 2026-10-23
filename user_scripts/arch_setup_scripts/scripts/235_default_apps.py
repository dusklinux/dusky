#!/usr/bin/env python3
#d: Select default applications or restore saved choices after an update
"""Dusky default apps. Python standard library only; run as the desktop user.

Menus: --file-manager, --browser, --text-editor, --terminal.
Direct selection: --terminal --set foot. Restore: --apply-state (alias --auto).
Selections synchronize Hyprland bindings, user MIME defaults, terminal launchers,
and installed FileManager1 providers. Existing file-manager windows stay open.
"""

import argparse
from configparser import ConfigParser, Error as ConfigError
from contextlib import contextmanager
from dataclasses import dataclass
import fcntl
import os
from pathlib import Path
import re
import shlex
import shutil
import signal
import stat
import subprocess
import sys
import tempfile


@dataclass(frozen=True, slots=True)
class App:
    key: str
    desktop: str
    label: str
    terminal: bool = False
    commands: tuple[str, ...] = ()

    @property
    def command(self) -> str:
        if not self.commands:
            return self.key
        return next((name for name in self.commands if shutil.which(name)), self.commands[0])


@dataclass(frozen=True, slots=True)
class Category:
    variable: str
    description: str
    binding: str
    state: str
    legacy: tuple[str, str]  # true, false
    apps: tuple[App, ...]

    def app(self, key: str) -> App:
        for app in self.apps:
            if key == app.key or key in app.commands:
                return app
        raise ValueError(f"Unknown {self.description.lower()} choice: {key!r}")


# Add choices here; CLI flags, menus and state restoration share this catalog.
CATEGORIES = {
    'file-manager': Category('fileManager', 'File Manager', 'SUPER + E', 'filemanager_switch', ('yazi', 'nemo'), (
        App('nemo', 'nemo.desktop', 'Nemo (GUI)', False),
        App('yazi', 'yazi.desktop', 'Yazi (Terminal)', True),
        App('thunar', 'thunar.desktop', 'Thunar (GUI)', False),
        App('dolphin', 'org.kde.dolphin.desktop', 'Dolphin (GUI)', False),
        App('nautilus', 'org.gnome.Nautilus.desktop', 'Nautilus (GUI)', False),
        App('pcmanfm', 'pcmanfm.desktop', 'PCManFM (GUI)', False),
        App('ranger', 'ranger.desktop', 'Ranger (Terminal)', True),
        App('lf', 'lf.desktop', 'Lf (Terminal)', True),
        App('superfile', 'superfile.desktop', 'Superfile (Terminal)', True, ('spf', 'superfile')),
    )),
    'browser': Category('browser', 'Launch Browser', 'SUPER + W', 'browser_switch', ('firefox', 'chromium'), (
        App('firefox', 'firefox.desktop', 'Firefox', False),
        App('chromium', 'chromium.desktop', 'Chromium', False),
        App('zen-browser', 'zen.desktop', 'Zen Browser', False),
        App('google-chrome-stable', 'google-chrome.desktop', 'Google Chrome', False),
        App('brave', 'brave-browser.desktop', 'Brave', False),
        App('helium-browser', 'helium.desktop', 'Helium', False),
        App('librewolf', 'librewolf.desktop', 'LibreWolf', False),
        App('edge', 'microsoft-edge.desktop', 'Microsoft Edge', False, ('microsoft-edge-stable', 'edge')),
        App('vivaldi', 'vivaldi-stable.desktop', 'Vivaldi', False),
        App('qutebrowser', 'org.qutebrowser.qutebrowser.desktop', 'Qutebrowser', False),
        App('lynx', 'lynx.desktop', 'Lynx (Terminal)', True),
    )),
    'text-editor': Category('textEditor', 'Open Text Editor', 'SUPER + R', 'texteditor_switch', ('nvim', 'gnome-text-editor'), (
        App('gnome-text-editor', 'org.gnome.TextEditor.desktop', 'GNOME Text Editor (GUI)', False),
        App('nvim', 'nvim.desktop', 'Neovim (Terminal)', True),
        App('nano', 'nano.desktop', 'Nano (Terminal)', True),
        App('code', 'code.desktop', 'VS Code (GUI)', False),
        App('zeditor', 'dev.zed.Zed.desktop', 'Zed (GUI)', False),
        App('vscodium', 'codium.desktop', 'VS Codium (GUI)', False, ('codium', 'vscodium')),
        App('helix', 'Helix.desktop', 'Helix (Terminal)', True, ('helix', 'hx')),
        App('kate', 'org.kde.kate.desktop', 'Kate (GUI)', False),
        App('emacs', 'emacs.desktop', 'Emacs (GUI)', False),
        App('micro', 'micro.desktop', 'Micro (Terminal)', True),
        App('mousepad', 'org.xfce.mousepad.desktop', 'Mousepad (GUI)', False),
    )),
    'terminal': Category('terminal', 'Launch Terminal', 'SUPER + Q', 'terminal_switch', ('kitty', 'foot'), (
        App('kitty', 'kitty.desktop', 'Kitty', False),
        App('foot', 'foot.desktop', 'Foot', False),
        App('alacritty', 'Alacritty.desktop', 'Alacritty', False),
        App('wezterm', 'org.wezfurlong.wezterm.desktop', 'WezTerm', False),
        App('ghostty', 'com.mitchellh.ghostty.desktop', 'Ghostty', False),
        App('konsole', 'org.kde.konsole.desktop', 'Konsole', False),
        App('gnome-terminal', 'org.gnome.Terminal.desktop', 'GNOME Terminal', False),
    )),
}

# Keep these categories aligned with the defaults in .config/mimeapps.list.
# Include file(1) spellings as well as shared-mime-info names: xdg-open uses both.
MIMES = frozendict({
    "file-manager": ("inode/directory",),
    "browser": (
        "x-scheme-handler/http", "x-scheme-handler/https", "x-scheme-handler/about",
        "x-scheme-handler/unknown", "x-scheme-handler/chrome", "text/html",
        "application/xhtml+xml",
    ),
    "text-editor": (
        "text/plain", "text/markdown", "text/x-shellscript", "application/x-shellscript",
        "text/x-python", "text/x-script.python", "text/x-go", "text/rust", "text/x-c",
        "text/x-c++", "text/x-csrc", "text/x-chdr", "text/x-c++src", "text/x-c++hdr",
        "text/x-lua", "text/x-java", "text/x-makefile", "text/x-moc", "text/x-pascal",
        "text/tcl", "text/x-tcl", "text/x-awk", "text/x-tex", "application/json",
        "application/toml", "application/yaml", "application/x-yaml", "text/yaml",
        "text/css", "text/javascript", "application/javascript", "text/xml",
        "application/xml", "application/x-zerosize", "inode/x-empty",
        "text/csv", "text/tab-separated-values", "text/calendar", "text/vcard",
        "application/ld+json", "application/json5", "application/schema+json",
        "application/json-patch+json", "application/x-ndjson", "application/rss+xml",
        "application/atom+xml", "application/rdf+xml", "application/sql",
        "application/typescript", "application/x-perl", "application/x-ruby",
        "application/x-php", "text/x-perl", "text/x-ruby", "text/x-php",
        "application/x-desktop", "text/x-log", "text/x-patch", "text/x-diff",
        "text/x-cmake", "text/x-meson", "text/x-systemd-unit", "text/x-dbus-service",
        "text/x-csharp", "text/x-objcsrc", "text/x-objc++src", "text/x-scala",
        "text/x-kotlin", "text/x-sass", "text/x-scss", "text/vnd.trolltech.linguist",
        "application/x-tiled-tsx",
    ),
})

PRIMARY_MIME = {
    "file-manager": "inode/directory",
    "browser": "x-scheme-handler/https",
    "text-editor": "text/plain",
}

# Tokenize just enough Lua to locate assignments and balanced calls/tables.
# Comments and quoted/long strings cannot masquerade as live bindings.
LUA_TOKEN = re.compile(
    r"(?P<comment>--(?:\[(?P<ceq>=*)\[.*?\](?P=ceq)\]|[^\n]*))"
    r"|(?P<string>'(?:\\.|[^'\\\r\n])*'|\"(?:\\.|[^\"\\\r\n])*\")"
    r"|(?P<long>\[(?P<seq>=*)\[.*?\](?P=seq)\])"
    r"|(?P<name>[A-Za-z_][A-Za-z_0-9]*)|(?P<symbol>[^\s])",
    re.DOTALL,
)


type Token = re.Match[str]


def tokens(text: str) -> list[Token]:
    result = []
    for token in LUA_TOKEN.finditer(text):
        value = token.group()
        if token.lastgroup == "symbol" and value in ("'", '"'):
            raise ValueError("Unterminated Lua string")
        if token.lastgroup == "symbol" and value == "[" and re.match(r"\[=*\[", text[token.start():]):
            raise ValueError("Unterminated Lua long string")
        if token.lastgroup == "comment":
            opening = re.match(r"--\[(=*)\[", value)
            if opening and not value.endswith("]" + opening[1] + "]"):
                raise ValueError("Unterminated Lua block comment")
        else:
            result.append(token)
    return result


def edits(text: str, changes: list[tuple[int, int, str]]) -> str:
    for start, end, value in sorted(changes, reverse=True):
        text = text[:start] + value + text[end:]
    return text


def assignment(text: str, variable: str) -> tuple[int, int, str] | None:
    ts = tokens(text)
    found = []
    for i, token in enumerate(ts):
        if token.group() != variable or i + 1 >= len(ts) or ts[i + 1].group() != "=":
            continue
        prefix = text[text.rfind("\n", 0, token.start()) + 1:token.start()]
        if not re.fullmatch(r"\s*(?:local\s+)?", prefix):
            continue
        if i + 2 >= len(ts):
            raise ValueError(f"Missing value for {variable}")
        value = ts[i + 2]
        if value.lastgroup != "string":
            raise ValueError(f"{variable} must be a quoted application name")
        if i + 3 < len(ts):
            following = ts[i + 3]
            continuation = following.group() in (".", "+", "-", "*", "/", "%", "^", "&", "|", "~", "<", ">", "=", "and", "or", "(", "[", "{") or following.lastgroup in ("string", "long")
            if continuation or ("\n" not in text[value.end():following.start()] and following.group() != ";"):
                raise ValueError(f"{variable} uses an expression; select a literal application name first")
        found.append((value.start(), value.end(), value.group()[1:-1]))
    if len(found) > 1:
        raise ValueError(f"Multiple assignments to {variable}; keep one active default")
    return found[0] if found else None


def defaults(text: str) -> dict[str, str]:
    result = {}
    for kind, cat in CATEGORIES.items():
        value = assignment(text, cat.variable)
        command = value[2] if value else "unknown"
        app = next((app for app in cat.apps if command == app.key or command in app.commands), None)
        result[kind] = app.key if app else command
    return result


def set_default(text: str, cat: Category, key: str) -> str:
    value = assignment(text, cat.variable)
    if value:
        return edits(text, [(value[0], value[1], f'"{key}"')])
    return text.rstrip("\n") + f'\n{cat.variable} = "{key}"\n'


def closing(ts: list[Token], start: int) -> int:
    pairs = {"(": ")", "{": "}", "[": "]"}
    stack = []
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


def sequence(ts: list[Token], i: int, values: tuple[str, ...]) -> bool:
    return tuple(t.group() for t in ts[i:i + len(values)]) == values


def launch_expression(kind: str, app: App, terminal: str) -> str:
    variable = CATEGORIES[kind].variable
    if not app.terminal:
        return f'"dusky-run " .. {variable}'
    # Existing manual terminal options remain in the Lua variable; use only
    # the executable name to choose its command/application-ID syntax.
    terminal_words = shlex.split(terminal)
    terminal = Path(terminal_words[0]).name if terminal_words else "unknown"
    # kitty/foot verified via installed help. Other terminals use upstream CLI:
    # alacritty: extra/man/alacritty.1.scd; wezterm.org/cli/start.html;
    # ghostty.org/docs/config/reference#command; Konsole/GNOME terminal CLI.
    # Keep editor app IDs used by existing Hyprland window rules.
    editor = kind == "text-editor"
    if terminal == "kitty":
        suffix = f'" --app-id " .. {variable} .. " "' if editor else '" "'
    elif terminal == "foot":
        suffix = f'" --app-id=" .. {variable} .. " "' if editor else '" "'
    elif terminal == "alacritty":
        suffix = f'" --class " .. {variable} .. " -e "' if editor else '" -e "'
    elif terminal == "wezterm":
        suffix = f'" start --class " .. {variable} .. " -- "' if editor else '" start -- "'
    elif terminal in ("ghostty", "konsole"):
        suffix = '" -e "'
    elif terminal == "gnome-terminal":
        suffix = '" -- "'
    else:
        raise ValueError(f"Unsupported terminal for {app.key}: {terminal!r}")
    return f'"dusky-run " .. terminal .. {suffix} .. {variable}'


def set_binding(text: str, cat: Category, expression: str) -> str:
    ts = tokens(text)
    changes = []
    found = False
    i = 0
    while i < len(ts):
        if not sequence(ts, i, ("hl", ".", "bind", "(")):
            i += 1
            continue
        end = closing(ts, i + 3)
        descriptions = [j for j in range(i + 4, end - 2)
                        if sequence(ts, j, ("description", "="))
                        and ts[j + 2].lastgroup == "string"
                        and ts[j + 2].group()[1:-1] == cat.description]
        if descriptions:
            found = True
            calls = [j for j in range(i + 4, end)
                     if sequence(ts, j, ("hl", ".", "dsp", ".", "exec_cmd", "("))]
            if len(calls) != 1 or len(descriptions) != 1:
                raise ValueError(f"Cannot locate one exec_cmd for {cat.description!r}")
            call = calls[0] + 5
            call_end = closing(ts, call)
            changes.append((ts[call].end(), ts[call_end].start(), expression))
            # Find the options table containing the description, preserving all
            # options including an explicitly set submap_universal = false.
            tables = [(j, closing(ts, j)) for j in range(i + 4, end) if ts[j].group() == "{"]
            tables = [(a, b) for a, b in tables if a < descriptions[0] < b]
            if not tables:
                raise ValueError(f"Missing options table for {cat.description!r}")
            a, b = max(tables)
            if not any(sequence(ts, j, ("submap_universal", "=")) for j in range(a + 1, b)):
                separator = "" if ts[b - 1].group() in (",", ";") else ","
                changes.append((ts[b - 1].end(), ts[b - 1].end(), separator + " submap_universal = true"))
        i = end + 1
    if found:
        return edits(text, changes)
    return (text.rstrip("\n") + '\n\n-- Auto-generated by Dusky Default Apps\nhl.bind(\n'
            f'    "{cat.binding}",\n    hl.dsp.exec_cmd({expression}),\n'
            f'    {{ description = "{cat.description}", submap_universal = true }}\n)\n')


def atomic_write(path: Path, data: bytes) -> None:
    """Replace a file durably, retaining its mode/owner and symlink target."""
    path = path.resolve()
    path.parent.mkdir(parents=True, exist_ok=True)
    original = path.stat() if path.exists() else None
    fd, name = tempfile.mkstemp(prefix=".default-apps-", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(data)
            stream.flush()
            if original:
                current = os.fstat(stream.fileno())
                if (current.st_uid, current.st_gid) != (original.st_uid, original.st_gid):
                    os.fchown(stream.fileno(), original.st_uid, original.st_gid)
                os.fchmod(stream.fileno(), stat.S_IMODE(original.st_mode))
            os.fsync(stream.fileno())
        os.replace(name, path)
        directory = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    finally:
        Path(name).unlink(missing_ok=True)


def write_batch(changes: dict[Path, bytes]) -> None:
    """Rollback completed writes on an ordinary I/O failure or interrupt.

    Each file replacement is atomic. This is not a multi-file power-loss
    transaction; saved preferences are written before the derived config.
    """
    originals = {p: p.read_bytes() if p.exists() else None for p in changes}
    written = []
    try:
        for path, content in changes.items():
            if originals[path] != content:
                written.append(path)
                atomic_write(path, content)
    except BaseException as error:
        failures = []
        for path in reversed(written):
            try:
                if originals[path] is None:
                    path.resolve().unlink(missing_ok=True)
                else:
                    atomic_write(path, originals[path])
            except OSError as rollback_error:
                failures.append(f"{path}: {rollback_error}")
        if failures:
            raise OSError("Write failed and rollback was incomplete: " + "; ".join(failures)) from error
        raise


@contextmanager
def config_lock(settings: Path):
    settings.mkdir(parents=True, exist_ok=True)
    # All categories and entry points share this inode; never unlink a lock.
    with (settings / ".default_apps.lock").open("a") as stream:
        try:
            fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise ValueError("Default app configuration is busy; try again") from None
        yield


def run_command(argv: list[str]) -> str:
    command = shutil.which(argv[0])
    if command is None:
        raise OSError(f"{argv[0]} is unavailable")
    try:
        result = subprocess.run([command, *argv[1:]], capture_output=True, encoding="utf-8", errors="replace", timeout=15)
    except (OSError, subprocess.TimeoutExpired) as error:
        raise OSError(f"{argv[0]}: {error}") from error
    if result.returncode:
        raise OSError(f"{argv[0]} exited {result.returncode}: {result.stderr.strip() or result.stdout.strip()}")
    return result.stdout


def data_roots(data_home: Path) -> tuple[Path, ...]:
    return (data_home, *(Path(p) for p in os.environ.get(
        "XDG_DATA_DIRS", "/usr/local/share:/usr/share").split(":") if p))


def desktop_file(app: App, data_home: Path) -> Path | None:
    return next((root / "applications" / app.desktop for root in data_roots(data_home)
                 if (root / "applications" / app.desktop).is_file()), None)


def mime_defaults(text: str, updates: dict[str, str]) -> str:
    """Update just defaults, preserving comments, other groups, and MIME case."""
    parser = ConfigParser(interpolation=None)
    parser.optionxform = str
    try:
        parser.read_string(text)
    except ConfigError as error:
        raise ValueError(f"Invalid MIME configuration: {error}") from error
    pending = dict(updates)
    output = []
    section = ""
    found = False
    for line in text.splitlines(keepends=True):
        stripped = line.strip()
        header = re.prefixmatch(r"\[([^]]+)\]", stripped)
        if header:
            if section == "Default Applications":
                output.extend(f"{key}={value}\n" for key, value in pending.items())
                pending.clear()
            section = header[1]
            found |= section == "Default Applications"
        if section == "Default Applications" and not stripped.startswith(("#", ";")) and "=" in line:
            key = line.split("=", 1)[0].strip()
            if key in updates:
                line = f"{key}={updates[key]}\n"
                pending.pop(key, None)
        output.append(line if line.endswith("\n") else line + "\n")
    if not found:
        output.append("\n[Default Applications]\n")
    output.extend(f"{key}={value}\n" for key, value in pending.items())
    return "".join(output)


def terminal_desktop(kind: str, app: App) -> bytes:
    """Use the selected default terminal for file/URI launches as well as keys."""
    argument = "%U" if kind == "browser" else "%F"
    categories = {"file-manager": "System;FileManager;", "browser": "Network;WebBrowser;",
                  "text-editor": "Utility;TextEditor;"}
    return ("[Desktop Entry]\nType=Application\n"
            f"Name={app.label}\nIcon={app.key}\nTryExec={app.command}\n"
            f"Exec=xdg-terminal-exec --app-id={app.key} -- {app.command} {argument}\n"
            f"Terminal=false\nCategories={categories[kind]}\n"
            f"MimeType={';'.join(MIMES[kind])};\n").encode()


def file_manager_service(app: App, data_home: Path) -> bytes | None:
    """Use the installed provider's activation command and systemd unit.

    Firefox's Show in Folder uses FileManager1, independently of MIME defaults.
    Terminal managers generally do not implement this interface; retain the
    existing GUI provider for them rather than invent an incompatible command.
    """
    destination = data_home / "dbus-1/services/org.freedesktop.FileManager1.service"
    commands = {name.casefold() for name in (app.key, *app.commands)}
    for root in data_roots(data_home):
        for path in sorted((root / "dbus-1/services").glob("*.service")):
            if path == destination:
                continue  # Do not mistake our previous selection for a vendor provider.
            parser = ConfigParser(interpolation=None)
            try:
                parser.read(path, encoding="utf-8")
            except ConfigError:
                continue  # An unrelated malformed service must not break selection.
            if not parser.has_section("D-BUS Service"):
                continue
            service = parser["D-BUS Service"]
            if service.get("Name") != "org.freedesktop.FileManager1":
                continue
            command = shlex.split(service.get("Exec", ""))
            if command and Path(command[0]).name.casefold() in commands:
                return path.read_bytes()
    return None


class Switcher:
    def __init__(self, home: Path | None = None):
        # Keep Dusky's config/state layout, including relocated XDG directories.
        self.config = Path(os.environ.get("XDG_CONFIG_HOME", str((home or Path.home()) / ".config")))
        self.variables = self.config / "hypr/edit_here/source/default_apps.lua"
        self.bindings = self.config / "hypr/edit_here/source/keybinds.lua"
        self.settings = self.config / "dusky/settings"
        self.data_home = Path(os.environ.get("XDG_DATA_HOME", str(
            (home or Path.home()) / ".local/share")))

    def current(self) -> dict[str, str]:
        return defaults(self.variables.read_text(encoding="utf-8"))

    def saved(self, kind: str) -> str | None:
        cat = CATEGORIES[kind]
        state = self.settings / cat.state
        smart = state.with_name(state.name + ".smart")
        if smart.exists():
            key = smart.read_text(encoding="utf-8").strip()
            cat.app(key)  # Reject invalid saved choices, rather than overwrite them.
            return key
        if state.exists():
            value = state.read_text(encoding="utf-8").strip()
            if value not in ("true", "false"):
                raise ValueError(f"Invalid legacy state: {state}")
            return cat.legacy[value == "false"]
        return None

    def apply(self, requested: dict[str, str] | None, kinds: list[str]) -> list[str]:
        with config_lock(self.settings):
            if requested is None:
                requested = {kind: key for kind in kinds if (key := self.saved(kind)) is not None}
            if not requested:
                return ["No saved choices found; nothing changed."]
            selected = {kind: CATEGORIES[kind].app(key) for kind, key in requested.items()}
            for app in selected.values():
                if not shutil.which(app.command):
                    raise ValueError(f"{app.label} is not installed ({app.command})")
                if not app.terminal:
                    path = desktop_file(app, self.data_home)
                    if path is None:
                        raise ValueError(f"Desktop entry is unavailable: {app.desktop}")
                    entry = ConfigParser(interpolation=None)
                    try:
                        entry.read(path, encoding="utf-8")
                        desktop = entry["Desktop Entry"]
                        if desktop.getboolean("Hidden", fallback=False):
                            raise ValueError(f"Desktop entry is hidden: {path}")
                        if desktop.get("TryExec") and not shutil.which(desktop["TryExec"]):
                            raise ValueError(f"Desktop entry requires an unavailable command: {path}")
                    except (ConfigError, KeyError) as error:
                        raise ValueError(f"Invalid desktop entry {path}: {error}") from error
            # Read and validate both inputs before making any writes.
            variables = self.variables.read_text(encoding="utf-8")
            bindings = self.bindings.read_text(encoding="utf-8")
            for kind, app in selected.items():
                variables = set_default(variables, CATEGORIES[kind], app.command)
            current = defaults(variables)
            binding_apps = dict(selected)
            if "terminal" in selected:
                # Refresh dependent commands using the final terminal, regardless
                # of restore order, without changing their selected applications.
                for kind in ("file-manager", "browser", "text-editor"):
                    if kind not in selected:
                        app = next((a for a in CATEGORIES[kind].apps if a.key == current[kind]), None)
                        if app and app.terminal:
                            binding_apps[kind] = app
            for kind, app in binding_apps.items():
                expression = launch_expression(kind, app, current["terminal"])
                bindings = set_binding(bindings, CATEGORIES[kind], expression)
            changes = {}
            for kind, app in selected.items():
                cat = CATEGORIES[kind]
                state = self.settings / cat.state
                changes[state.with_name(state.name + ".smart")] = (app.key + "\n").encode()
                legacy = app.terminal if kind in ("file-manager", "text-editor") else app.key == cat.legacy[0]
                changes[state] = (str(legacy).lower() + "\n").encode()
            changes[self.variables] = variables.encode()
            changes[self.bindings] = bindings.encode()
            desktop_names = tuple(name.lower() for name in os.environ.get(
                "XDG_CURRENT_DESKTOP", "Hyprland").split(":") if name)
            updates = {mime: app.desktop for kind, app in selected.items()
                       for mime in MIMES.get(kind, ())}
            if updates:
                path = self.config / "mimeapps.list"
                text = path.read_text(encoding="utf-8") if path.exists() else ""
                changes[path] = mime_defaults(text, updates).encode()
                # Desktop-specific user overrides take precedence over mimeapps.list.
                for name in desktop_names:
                    path = self.config / f"{name}-mimeapps.list"
                    if path.exists():
                        text = path.read_text(encoding="utf-8")
                        parser = ConfigParser(interpolation=None)
                        parser.optionxform = str
                        try:
                            parser.read_string(text)
                        except ConfigError as error:
                            raise ValueError(f"Invalid MIME configuration in {path}: {error}") from error
                        existing = parser["Default Applications"] if parser.has_section("Default Applications") else {}
                        overrides = {mime: value for mime, value in updates.items() if mime in existing}
                        if overrides:
                            changes[path] = mime_defaults(text, overrides).encode()
            terminal_apps = {kind: app for kind, app in binding_apps.items() if app.terminal}
            if terminal_apps:
                for command in ("xdg-terminal-exec", "update-desktop-database"):
                    if not shutil.which(command):
                        raise ValueError(f"{command} is required for terminal application launchers")
                for kind, app in terminal_apps.items():
                    changes[self.data_home / "applications" / app.desktop] = terminal_desktop(kind, app)
            if "terminal" in selected or terminal_apps:
                command = Path(shlex.split(current["terminal"])[0]).name
                terminal_app = CATEGORIES["terminal"].app(command)
                paths = [self.config / "xdg-terminals.list"]
                paths.extend(self.config / f"{name}-xdg-terminals.list" for name in desktop_names
                             if (self.config / f"{name}-xdg-terminals.list").exists())
                for path in paths:
                    lines = path.read_text(encoding="utf-8").splitlines() if path.exists() else []
                    lines = [line for line in lines if line.strip().lstrip("+-") != terminal_app.desktop]
                    changes[path] = (terminal_app.desktop + "\n" + "\n".join(lines).rstrip("\n") + "\n").encode()
            provider = None
            notes = []
            if "file-manager" in selected:
                provider = file_manager_service(selected["file-manager"], self.data_home)
                if provider is not None:
                    changes[self.data_home / "dbus-1/services/org.freedesktop.FileManager1.service"] = provider
                else:
                    notes.append("This file manager has no installed FileManager1 provider; Show in Folder keeps its existing GUI provider.")
            write_batch(changes)
            failures = []
            if "terminal" in selected or terminal_apps:
                try:
                    # Its cache hashes file listings, so reordering equal-sized
                    # terminal lists can otherwise leave the previous choice cached.
                    cache = Path(os.environ.get("XDG_CACHE_HOME", str(Path.home() / ".cache")))
                    (cache / "xdg-terminal-exec").unlink(missing_ok=True)
                except OSError as error:
                    failures.append(str(error))
            if terminal_apps:
                try:
                    run_command(["update-desktop-database", str(self.data_home / "applications")])
                except OSError as error:
                    failures.append(str(error))
            for kind, app in selected.items():
                if kind in MIMES:
                    try:
                        mime = PRIMARY_MIME[kind]
                        actual = run_command(["xdg-mime", "query", "default", mime]).strip()
                        if actual != app.desktop:
                            failures.append(f"{mime}: expected {app.desktop}, got {actual or 'no handler'}; check installed desktop entries and MIME overrides")
                    except OSError as error:
                        failures.append(str(error))
            if provider is not None and os.environ.get("DBUS_SESSION_BUS_ADDRESS"):
                try:
                    bus = ["busctl", "--user", "call", "org.freedesktop.DBus",
                           "/org/freedesktop/DBus", "org.freedesktop.DBus"]
                    run_command([*bus, "ReloadConfig"])
                    run_command([*bus, "StartServiceByName", "su", "org.freedesktop.FileManager1", "0"])
                    pid = int(run_command([*bus, "GetConnectionUnixProcessID", "s",
                                           "org.freedesktop.FileManager1"]).split()[1])
                    executable = Path(f"/proc/{pid}/exe").resolve().name.casefold()
                    app = selected["file-manager"]
                    if executable not in {name.casefold() for name in (app.key, *app.commands)}:
                        notes.append("Another file manager still owns Show in Folder; close it or log in again to activate the selected provider.")
                except OSError as error:
                    failures.append(str(error))
            # xdg-settings has no Hyprland backend; its generic handler repeats
            # the MIME updates above. Avoid this redundant subprocess.
            if os.environ.get("HYPRLAND_INSTANCE_SIGNATURE"):
                try:
                    run_command(["hyprctl", "reload", "config-only"])
                except OSError as error:
                    failures.append(str(error))
            if failures:
                raise ValueError("Choices saved, but application is incomplete: " + "; ".join(failures))
            return [f"{CATEGORIES[kind].description}: {app.label}" for kind, app in selected.items()] + notes


def menu(switcher: Switcher, category: str | None) -> tuple[int, str]:
    import curses
    import termios

    if not sys.stdin.isatty() or not sys.stdout.isatty():
        raise ValueError("Menus require a terminal; use --set NAME or --apply-state")

    def interact(screen):
        mode = termios.tcgetattr(sys.stdin.fileno())
        mode[0] &= ~termios.IXON
        termios.tcsetattr(sys.stdin.fileno(), termios.TCSANOW, mode)
        try:
            curses.curs_set(0)
        except curses.error:
            pass  # Some terminal descriptions cannot change cursor visibility.
        curses.set_escdelay(80)
        curses.mousemask(curses.ALL_MOUSE_EVENTS)
        screen.keypad(True)
        normal = accent = success = error_style = muted = 0
        if curses.has_colors():
            # ANSI black can be grey in the user's palette. Pair zero and all
            # menu colours must use the terminal's real default background.
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
        if kind:
            selected = next((i for i, app in enumerate(CATEGORIES[kind].apps)
                             if app.key == current[kind]), 0)
        while True:
            screen.erase()
            height, width = screen.getmaxyx()
            small = height < 14 or width < 40
            left, right = 2, width - 3
            content_width = max(0, right - left + 1)
            def put(row, text, attr=normal, column=left):
                if 0 <= row < height - 1 and 0 <= column < width - 1:
                    screen.addnstr(row, column, text, max(0, width - column - 1), attr)
            choices = list(CATEGORIES) if kind is None else list(CATEGORIES[kind].apps)
            visible = max(1, min(10, height - 11))
            offset = max(0, min(offset, selected))
            if selected >= offset + visible:
                offset = selected - visible + 1
            if small:
                put(0, "Resize to at least 40 columns x 14 rows.", column=0)
                put(1, "q: quit")
            else:
                title = "Default Applications" if kind is None else CATEGORIES[kind].description.removeprefix("Launch ").removeprefix("Open ")
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
                    active = bool(kind and choice.key == current[kind])
                    if kind is None:
                        label = CATEGORIES[choice].description.removeprefix("Launch ").removeprefix("Open ")
                        detail = current[choice]
                    else:
                        label = choice.label.partition(" (")[0]
                        app_type = "" if kind == "terminal" else "TUI" if choice.terminal else "GUI"
                        detail = " · ".join(part for part in (app_type, "ACTIVE" if active else "") if part)
                    detail = detail[:max(0, content_width // 2 - 2)]
                    highlighted = i == selected
                    style = accent | curses.A_REVERSE | curses.A_BOLD if highlighted else normal
                    put(row, " " * content_width, style)
                    label_width = max(1, content_width - len(detail) - 6)
                    label = label if len(label) <= label_width else label[:label_width - 1] + "…"
                    put(row, ("› " if highlighted else "  ") + label, style, left + 1)
                    if detail:
                        put(row, detail, style if highlighted else success if active else muted, right - len(detail))
                put(height - 5, "─" * content_width, muted)
                message = status or ("Choose an application to make it your default." if kind else "Selections are saved and restored after updates.")
                put(height - 4, message, (error_style if outcome[0] else success) if status else muted)
                put(height - 3, "↑↓ / j k  Move    Enter  " + ("Open" if kind is None else "Apply"), muted)
                put(height - 2, "Esc  Back    q  Quit    Click right to apply", muted)
            screen.refresh()
            key = screen.getch()  # Block until input/resize; no idle redraw loop.
            if key == -1:
                raise ValueError("Terminal input closed")
            if key in (ord("q"), ord("Q")):
                return outcome
            if key == 27:
                if kind is None or category is not None:
                    return outcome
                kind, selected, offset, status = None, 0, 0, ""
                continue
            if small or key == curses.KEY_RESIZE:
                continue
            apply = key in (10, 13, curses.KEY_ENTER)
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
                        apply = x >= min(38, width - 10) or bool(button & curses.BUTTON1_DOUBLE_CLICKED)
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
            if apply:
                if kind is None:
                    kind = choices[selected]
                    selected = next((i for i, app in enumerate(CATEGORIES[kind].apps)
                                     if app.key == current[kind]), 0)
                    offset = 0
                    status = ""
                else:
                    try:
                        status = "; ".join(switcher.apply({kind: choices[selected].key}, [kind]))
                        outcome = (0, status)
                    except (OSError, ValueError) as error:
                        status = f"Error: {error}"
                        outcome = (1, status)
                    current = switcher.current()
    try:
        return curses.wrapper(interact)
    except (curses.error, termios.error) as error:
        raise ValueError(f"Cannot use this terminal: {error}") from error


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter, allow_abbrev=False)
    group = parser.add_mutually_exclusive_group()
    for kind in CATEGORIES:
        aliases = [f"--{kind}"]
        if kind == "text-editor":
            aliases.append("--editor")
        group.add_argument(*aliases, dest="category", action="store_const", const=kind)
    actions = parser.add_mutually_exclusive_group()
    actions.add_argument("--set", metavar="NAME", help="Set a catalog choice in the selected category")
    actions.add_argument("--apply-state", "--auto", action="store_true", help="Restore saved choices without a menu")
    args = parser.parse_args(argv)
    if args.set and not args.category:
        parser.error("--set requires a category flag")
    return args


def interrupted(signum, frame):
    raise SystemExit(128 + signum)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(sys.argv[1:] if argv is None else argv)
    for signum in (signal.SIGHUP, signal.SIGTERM):
        signal.signal(signum, interrupted)
    switcher = Switcher()
    try:
        if args.apply_state or args.set:
            kinds = [args.category] if args.category else list(CATEGORIES)
            requested = {args.category: args.set} if args.set else None
            for message in switcher.apply(requested, kinds):
                print(message)
        else:
            code, message = menu(switcher, args.category)
            if message:
                print(message, file=sys.stderr if code else sys.stdout)
            return code
        return 0
    except (OSError, ValueError) as error:
        print(f"Error: {error}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        return 130


if __name__ == "__main__":
    sys.exit(main())
