#!/usr/bin/env python3
"""Generate compact Lua window rules from Hyprland's Wayland clients.

Enter appends the essential template. Only float, size and move are enabled
initially, as in the original generator. Other essentials are editable comments;
optional properties are absent until selected with o. No third-party Python UI.
Preset choices are shown below the selected setting; p opens the full picker.
Number keys apply a preset directly; e accepts custom Lua expressions.
The window list refreshes automatically; options freeze the list while editing.

Rule reference: https://wiki.hypr.land/Configuring/Basics/Window-Rules/
"""

import argparse
lazy import curses
lazy import fcntl
import json
import os
from pathlib import Path
import re
import subprocess
import sys
lazy import tempfile
lazy import textwrap
from time import monotonic
lazy import unicodedata
from dataclasses import dataclass, field

TARGET_FILE = Path(os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config") / "hypr/edit_here/source/window_rules.lua"
APP_TITLE = "Dusky Window Rules"


@dataclass(frozen=True, slots=True)
class Property:
    key: str
    value: str
    description: str
    essential: bool = False


# Values are Lua expressions, not Python/JSON. Dynamic examples use documented
# types; hardware-specific choices (monitor/workspace) come from live IPC.
PROPERTIES = (
    Property("name", '"unnamed"', "Unique rule name", True),
    Property("match.class", '""', "Exact current class; static effects match the class at opening", True),
    Property("match.title", '""', "Exact current title; static effects match the title at opening", True),
    Property("float", "true", "Float the window", True),
    Property("size", "{800, 600}", "Pixels / proportions / 95%: choose below or p; e edits custom math", True),
    Property("move", "{0, 0}", "Placement presets below or p; e edits custom position math", True),
    Property("pin", "true", "Show on all workspaces (requires floating)", True),
    Property("center", "true", "Center floating window; use instead of move", True),
    Property("workspace", '"unset"', 'Workspace selector, optionally silent; "unset" removes assignment', True),
    Property("opaque", "true", "Set compositor opacity to 1; app transparency remains", True),
    Property("no_blur", "true", "Disable background blur", True),
    Property("no_focus", "true", "Prevent focus", True),
    Property("no_dim", "true", "Disable inactive dimming", True),
    Property("border_size", "2", "Border thickness in logical pixels", True),
    Property("animation", '"popin 87%"', "p: animation styles; e: custom style (fade-only uses popin 100%)", True),
    Property("focus_on_activate", "false", "Focus when the app requests activation", True),
    Property("xray", "true", "Blur through to wallpaper", True),
    Property("enabled", "true", "Enable this named rule"),
    Property("match.initial_class", '""', "Exact class at first map"),
    Property("match.initial_title", '""', "Exact title at first map"),
    Property("match.content", '"video"', "Content type: none, photo, video, game"),
    Property("match.focus", "true", "Match focused windows"),
    Property("match.fullscreen", "true", "Match fullscreen or maximized windows"),
    Property("match.fullscreen_state_client", "2", "Client fullscreen state: 0 none, 1 maximize, 2 full, 3 both"),
    Property("match.fullscreen_state_internal", "2", "Internal fullscreen state: 0, 1, 2, 3"),
    Property("match.float", "true", "Match floating windows"),
    Property("match.group", "true", "Match grouped windows"),
    Property("match.modal", "true", "Match modal windows"),
    Property("match.pin", "true", "Match pinned windows"),
    Property("match.tag", '"gaming"', "Match a tag"),
    Property("match.workspace", '"1"', "Match a workspace selector"),
    Property("match.xdg_tag", '".*"', "Match an xdgTag using RE2"),
    Property("content", '"game"', "Set content type: none, photo, video, game"),
    Property("fullscreen", "true", "Fullscreen on opening"),
    Property("fullscreen_state", '"0 0"', "Internal and client fullscreen states, each 0..3"),
    Property("group", '"set"', "p: presets; e: set, new, lock, barred, deny, invade, override, unset"),
    Property("maximize", "true", "Maximize on opening"),
    Property("monitor", '""', "Output name, optionally followed by silent"),
    Property("no_close_for", "500", "Ignore killactive for this many ms after opening"),
    Property("no_initial_focus", "true", "Do not focus on opening"),
    Property("pseudo", "true", "Pseudotile the window"),
    Property("scrolling_width", "1.0", "Initial column width in the scrolling layout"),
    Property("suppress_event", '"maximize"', "Block events: fullscreen, maximize, activate, activatefocus, fullscreenoutput"),
    Property("tile", "true", "Force tiling; use instead of float"),
    Property("border_color", '"rgb(33ccff)"', 'p: colors/gradient; e: color or {colors={"rgb(33ccff)","rgb(00ff99)"},angle=45}'),
    Property("confine_pointer", "true", "Confine cursor to the window"),
    Property("dim_around", "true", "Dim other windows around a floating window"),
    Property("decorate", "false", "Draw window decorations"),
    Property("idle_inhibit", '"fullscreen"', "Inhibit idle: none, always, focus, fullscreen"),
    Property("immediate", "true", "Allow tearing (also requires compositor/output support)"),
    Property("keep_aspect_ratio", "true", "Keep aspect ratio during mouse resizing"),
    Property("max_size", "{1600, 1000}", "Maximum floating size; numbers or quoted expressions"),
    Property("min_size", "{200, 100}", "Minimum floating size; numbers or quoted expressions"),
    Property("nearest_neighbor", "true", "Use nearest-neighbor filtering"),
    Property("no_anim", "true", "Disable animations"),
    Property("no_auto_hdr", "true", "Disable AutoHDR"),
    Property("no_follow_mouse", "true", "Do not focus on pointer entry with follow_mouse=1"),
    Property("no_glow", "true", "Disable glow"),
    Property("no_max_size", "true", "Remove application maximum size constraints"),
    Property("no_screen_share", "true", "Render black in screen sharing"),
    Property("no_shadow", "true", "Disable shadows"),
    Property("no_shortcuts_inhibit", "true", "Prevent app shortcut inhibition"),
    Property("no_vrr", "true", "Disable per-window VRR (misc.vrr=2 or 3)"),
    Property("no_wobble", "true", "Disable wobble"),
    Property("no_xdg_drags", "true", "Disable XDG-driven window drags"),
    Property("opacity", '"0.95 override 0.85 override"', "p: presets; e: active/inactive/fullscreen opacity; override sets absolute values"),
    Property("force_rgbx", "true", "Disable the app's own transparency"),
    Property("persistent_size", "true", "Remember floating size for the same class and title"),
    Property("render_unfocused", "true", "Render even when not visible"),
    Property("rounding", "10", "Corner radius in logical pixels"),
    Property("rounding_power", "2.0", "Corner curve power (2 is circular)"),
    Property("scroll_mouse", "1.0", "Mouse scroll factor"),
    Property("scroll_touchpad", "1.0", "Touchpad scroll factor"),
    Property("stay_focused", "true", "Keep focus as long as visible"),
    Property("sync_fullscreen", "true", "Synchronize internal and client fullscreen modes"),
    Property("tag", '"+gaming"', "Add/remove a dynamic tag using +/-, or toggle without a prefix"),
    Property("tonemap", '"off"', "Tonemapping: on, off, clamp, limited"),
)
PROPERTY_MAP = frozendict((p.key, p) for p in PROPERTIES)
DEFAULT_ACTIVE = frozenset({"name", "match.class", "float", "size", "move"})
# Hyprland evaluates these in logical monitor-local coordinates, using the
# requested window size for move expressions. Borders/reserved bars are not
# subtracted from monitor_w/monitor_h. Keep all expressions quoted Lua strings.
VALUE_PRESETS = frozendict({
    'size': (
        ('95% of monitor', '{"monitor_w * 0.95", "monitor_h * 0.95"}'),
    ),
    'move': (
        ('Centered on monitor', '{"(monitor_w - window_w) / 2", "(monitor_h - window_h) / 2"}'),
        ('Bottom-center, 20px gap', '{"(monitor_w - window_w) / 2", "monitor_h - window_h - 20"}'),
        ('Bottom-right, 20px gaps', '{"monitor_w - window_w - 20", "monitor_h - window_h - 20"}'),
        ('Centered on cursor', '{"cursor_x - window_w / 2", "cursor_y - window_h / 2"}'),
    ),
    'animation': tuple(('Fade only' if style == 'popin 100%' else style, f'"{style}"') for style in (
        'popin', 'popin 60%', 'popin 70%', 'popin 80%', 'popin 87%',
        'popin 90%', 'popin 95%', 'slide', 'slide top', 'slide bottom',
        'slide left', 'slide right', 'gnomed', 'popin 100%',
    )),
    'opacity': (
        ('Active/inactive 90%', '"0.9 override 0.9 override"'),
        ('Active 100%, inactive 85%', '"1.0 override 0.85 override"'),
        ('Multiply opacity by 95%', '"0.95"'),
    ),
    'border_color': (
        ('Red', '"rgb(ff0000)"'),
        ('Cyan with alpha', '"rgba(33ccffee)"'),
        ('Cyan/green gradient', '{colors = {"rgba(33ccffee)", "rgba(00ff99ee)"}, angle = 45}'),
    ),
    'group': tuple((style, f'"{style}"') for style in ('set', 'set lock', 'barred', 'deny')),
})
MONITOR_DIMENSIONS_QUERY = '''local rows = {}
for _, m in ipairs(hl.get_monitors()) do
    rows[#rows+1] = string.format("[%d,%d,%d]", m.id, m.mode.width, m.mode.height)
end
return "[" .. table.concat(rows, ",") .. "]"'''


@dataclass(slots=True)
class Window:
    address: str
    app_class: str
    title: str
    values: dict[str, str]
    monitor_id: int | None = None
    geometry: tuple[int, int, int, int] | None = None  # logical w, h, local x, y


@dataclass(slots=True)
class Selection:
    # No optional property is emitted until it is explicitly enabled.
    active: set[str] = field(default_factory=lambda: set(DEFAULT_ACTIVE))
    edits: dict[str, str] = field(default_factory=dict)

    @classmethod
    def for_window(cls, window: Window) -> Selection:
        selection = cls()
        if not window.app_class:
            selection.active.remove('match.class')
            selection.active.add('match.title')
        return selection

    def value(self, window: Window, key: str) -> str:
        return self.edits.get(key, window.values.get(key, PROPERTY_MAP[key].value))

    def enable(self, key: str) -> None:
        self.active.add(key)
        # Avoid ambiguous/conflicting geometry and layout examples.
        if key in ('center', 'tile', 'float') and self.edits.get(key, PROPERTY_MAP[key].value).strip() != 'true':
            return
        for a, b in (("center", "move"), ("tile", "float")):
            if key == a and self.edits.get(b, PROPERTY_MAP[b].value).strip() != 'false':
                self.active.discard(b)
            elif key == b and self.edits.get(a, PROPERTY_MAP[a].value).strip() == 'true':
                self.active.discard(a)


def lua_string(value: str) -> str:
    """Quote Lua strings without changing titles, regex bytes or control chars."""
    return '"' + ''.join(
        '\\\\' if ch == '\\' else '\\"' if ch == '"' else
        f'\\{ord(ch):03d}' if ord(ch) < 32 or ord(ch) == 127 else
        f'\\u{{{ord(ch):x}}}' if not ch.isprintable() else ch
        for ch in value
    ) + '"'


def literal_regex(value: str) -> str:
    # Escape RE2 metacharacters, then independently quote the Lua string.
    return lua_string('^(' + ''.join('\\' + c if c in r'\.[]*^$()+?{}|' else c for c in value) + ')$')


def display_text(value: str) -> str:
    return ''.join(c if c.isprintable() else ' ' for c in value)


def lua_tokens(text: str) -> list[str | bytes]:
    """Tokenize literal rule definitions, ignoring Lua comments and whitespace.

    Strings become their actual bytes, so quote style and equivalent Lua
    escapes compare equally. This inspects direct calls with literal names;
    computed names/aliases retain their normal Hyprland semantics.
    """
    pattern = re.compile(
        r'(?P<comment>--\[(?P<ceq>=*)\[.*?\](?P=ceq)\]|--[^\r\n]*)'
        r'|(?P<long>\[(?P<eq>=*)\[.*?\](?P=eq)\])'
        r'''|(?P<short>"(?:\\.|[^"\\])*"|'(?:\\.|[^'\\])*')'''
        r'|(?P<space>\s+)|[A-Za-z_][A-Za-z_0-9]*|.', re.DOTALL)
    escapes = frozendict(zip('abfnrtv\\\'"', (b'\a', b'\b', b'\f', b'\n', b'\r', b'\t', b'\v', b'\\', b"'", b'"'), strict=True))
    tokens = []
    for match in pattern.finditer(text):
        token = match.group()
        if match.lastgroup in ('comment', 'space'):
            continue
        if match.lastgroup == 'long':
            edge = len(match['eq']) + 2
            body = re.sub(r'\r\n|\n\r|\r', '\n', token[edge:-edge])
            if body.startswith('\n'):
                body = body[1:]
            tokens.append(body.encode('utf-8'))
        elif match.lastgroup == 'short':
            if '\\' not in token:
                tokens.append(token[1:-1].encode('utf-8'))
                continue
            value = bytearray()
            i = 1
            while i < len(token) - 1:
                ch = token[i]
                i += 1
                if ch != '\\':
                    value.extend(ch.encode('utf-8'))
                    continue
                ch = token[i]
                i += 1
                if ch in escapes:
                    value.extend(escapes[ch])
                elif ch in '0123456789':
                    start = i - 1
                    while i < min(start + 3, len(token) - 1) and token[i] in '0123456789':
                        i += 1
                    value.append(int(token[start:i]))
                elif ch == 'x':
                    value.append(int(token[i:i + 2], 16))
                    i += 2
                elif ch == 'u' and token[i] == '{':
                    end = token.index('}', i)
                    point = int(token[i + 1:end], 16)
                    if not 0 <= point <= 0x7fffffff:
                        raise ValueError('Invalid Lua Unicode escape in rule file')
                    # Lua permits UTF-8 escapes through 0x7fffffff, including
                    # surrogate values and five/six-byte sequences.
                    if point < 128:
                        value.append(point)
                    else:
                        length = max(2, (point.bit_length() + 3) // 5)
                        value.append(((0xff << (8 - length)) & 0xff) | (point >> (6 * (length - 1))))
                        value.extend(0x80 | ((point >> shift) & 0x3f)
                                     for shift in range(6 * (length - 2), -1, -6))
                    i = end + 1
                elif ch == 'z':
                    while i < len(token) - 1 and token[i] in ' \t\r\n\v\f':
                        i += 1
                elif ch in '\r\n':
                    if i < len(token) - 1 and token[i] in '\r\n' and token[i] != ch:
                        i += 1
                    value.extend(b'\n')
                else:
                    raise ValueError('Invalid Lua string escape in rule file')
            tokens.append(value.take_bytes())
        else:
            tokens.append(token)
    return tokens


def literal_rule_names(tokens: list[str | bytes]) -> list[bytes]:
    """Find literal name fields at the root of direct hl.window_rule tables."""
    names = []
    depth = 0
    for i, token in enumerate(tokens):
        if not depth:
            if token != '{':
                continue
            if tokens[max(0, i - 4):i] != ['hl', '.', 'window_rule', '('] and tokens[max(0, i - 3):i] != ['hl', '.', 'window_rule']:
                continue
            depth = 1
        elif token == '{':
            depth += 1
        elif token == '}':
            depth -= 1
        elif depth == 1 and tokens[i - 1] in ('{', ',', ';'):
            field = tokens[i:i + 4]
            if tokens[i:i + 3] == ['[', b'name', ']']:
                field = ['name', *tokens[i + 3:i + 6]]
            if len(field) == 4 and field[:2] == ['name', '='] and isinstance(field[2], bytes) and field[3] in (',', ';', '}'):
                names.append(field[2])
    return names


def hyprctl_json(command: str, *args: str) -> list:
    result = subprocess.run(["hyprctl", "-j", command, *args], capture_output=True,
                            text=True, check=True, timeout=3)
    data = json.loads(result.stdout)
    if not isinstance(data, list):
        raise ValueError(f"hyprctl {command}: expected a JSON list")
    return data


def preset_choices(window: Window, key: str) -> list[tuple[str, str]]:
    choices = [('Captured/default value', window.values.get(key, PROPERTY_MAP[key].value))]
    if key in ('size', 'move'):
        if 'relative_size' not in window.values:
            if window.monitor_id is None or window.geometry is None:
                raise ValueError('Refresh windows before capturing monitor proportions')
            dimensions = hyprctl_json('repl', MONITOR_DIMENSIONS_QUERY)
            monitor = next((row for row in dimensions if row[0] == window.monitor_id), None)
            if monitor is None or monitor[1] <= 0 or monitor[2] <= 0:
                raise ValueError('Monitor is unavailable; refresh windows')
            # Query actual logical dimensions: IPC scale is rounded, and
            # physical/scale division also misses Hyprland's size rounding.
            width, height = monitor[1:]
            w, h, x, y = window.geometry
            for prop, a, b in (('size', w, h), ('move', x, y)):
                window.values['relative_' + prop] = (
                    f'{{"monitor_w * ({a} / {width})", "monitor_h * ({b} / {height})"}}')
        choices[0] = ('Captured pixels', choices[0][1])
        choices.append(('Captured monitor proportions', window.values['relative_' + key]))
    seen = {value for _, value in choices}
    for label, value in VALUE_PRESETS[key]:
        if value not in seen:
            choices.append((label, value))
            seen.add(value)
    return choices


def scan_windows() -> list[Window]:
    try:
        monitors = {m['id']: m for m in hyprctl_json("monitors")}
        windows = []
        for c in hyprctl_json("clients"):
            if not c.get("mapped") or c.get("xwayland") or c.get("monitor") not in monitors:
                continue
            app_class = c.get("class") or c.get("initialClass") or ""
            title = c.get("title") or ""
            if not app_class and not title:
                continue
            m = monitors[c['monitor']]
            w, h = c['size']
            if w <= 0 or h <= 0:
                continue  # No usable geometry; never fabricate a captured size.
            x, y = c['at']
            ws = c.get('workspace', {})
            # Current IPC uses an empty name for numbered workspaces.
            ws_name = ws.get('name') or str(ws.get('id', ''))
            if ws_name and not ws_name.startswith(('special:', 'name:')) and not re.fullmatch(r'-?\d+', ws_name):
                ws_name = 'name:' + ws_name
            name = re.sub(r'[^A-Za-z0-9_-]+', '-', app_class or title).strip('-') or 'unnamed'
            values = {
                'name': lua_string(name), 'match.class': literal_regex(app_class),
                'match.title': literal_regex(title),
                'match.initial_class': literal_regex(c.get('initialClass') or app_class),
                'match.initial_title': literal_regex(c.get('initialTitle') or title),
                'size': f'{{{w}, {h}}}', 'move': f'{{{x - m["x"]}, {y - m["y"]}}}',
                'workspace': lua_string(ws_name or 'unset'),
                'match.workspace': lua_string(ws_name or '1'), 'monitor': lua_string(m['name']),
            }
            windows.append(Window(c['address'], app_class, title, values,
                                  c['monitor'], (w, h, x - m['x'], y - m['y'])))
        return windows
    except (KeyError, TypeError, AttributeError, ValueError) as exc:
        raise ValueError(f"Invalid Hyprland IPC data: {exc}") from exc


def generate_lua_rule(window: Window, selection: Selection | None = None) -> str:
    selection = selection if selection is not None else Selection.for_window(window)
    if not any(k.startswith('match.') for k in selection.active):
        raise ValueError("Enable at least one match property")
    lines = [f'-- {display_text(window.title or window.app_class)}', 'hl.window_rule({']
    for p in PROPERTIES:
        if p.key.startswith('match.') or not (p.essential or p.key in selection.active):
            continue
        prefix = '' if p.key in selection.active else '-- '
        if p.key == 'name':
            lines.append(f'    {prefix}name = {selection.value(window, p.key)},')
            lines.append('    match = {')
            for match in PROPERTIES:
                if match.key.startswith('match.') and (match.essential or match.key in selection.active):
                    comment = '' if match.key in selection.active else '-- '
                    lines.append(f'        {comment}{match.key[6:]} = {selection.value(window, match.key)},')
            lines.append('    },')
        else:
            lines.append(f'    {prefix}{p.key} = {selection.value(window, p.key)},')
    lines.append('})')
    return '\n'.join(lines) + '\n'


def validate_rule(text: str) -> None:
    """Use the real Hyprland parser in a separate process; never change live rules."""
    with tempfile.TemporaryDirectory(prefix='dusky-rule-') as directory:
        path = Path(directory) / 'rule.lua'
        path.write_text(text, encoding='utf-8')
        result = subprocess.run(['Hyprland', '--verify-config', '-c', str(path)],
                                capture_output=True, text=True, timeout=10)
    output = re.sub(r'\x1b\[[0-9;]*m', '', result.stdout + result.stderr)
    if result.returncode:
        detail = output.split('Config parsing result:')[-1].strip()
        raise ValueError(detail.replace(str(path), 'rule.lua') or 'Hyprland rejected this rule')


def append_rule(text: str, target: Path) -> bool:
    validate_rule(text)
    tokens = lua_tokens(text)
    names = literal_rule_names(tokens)
    if len(names) != len(set(names)):
        raise ValueError('Selected windows have duplicate rule names; select an exact address from --list')
    target.parent.mkdir(parents=True, exist_ok=True)
    # Lock the actual target, preserve its inode/symlink, and serialize appends.
    with target.open('a+b', buffering=0) as stream:
        fcntl.flock(stream, fcntl.LOCK_EX)
        stream.seek(0)
        existing_bytes = stream.read()
        existing = existing_bytes.decode('utf-8')
        existing_tokens = lua_tokens(existing)
        if any(existing_tokens[i:i + len(tokens)] == tokens for i, t in enumerate(existing_tokens) if t == 'hl'):
            return False
        # Named rules merge in Hyprland. Refuse accidental reuse rather than
        # silently replacing another rule's match conditions/effects.
        if set(names).intersection(literal_rule_names(existing_tokens)):
            raise ValueError('Rule name already exists; edit name in options or use --option name=\'"another-name"\'')
        stream.seek(0, os.SEEK_END)
        separator = '' if not existing or existing.endswith('\n\n') else '\n' if existing.endswith('\n') else '\n\n'
        payload = memoryview((separator + text).encode('utf-8'))
        try:
            while payload:
                written = stream.write(payload)
                if not written:
                    raise OSError('Could not complete rule append')
                payload = payload[written:]
            os.fsync(stream.fileno())
        except BaseException:
            # A failed/short write must not leave half a Lua block behind.
            stream.truncate(len(existing_bytes))
            os.fsync(stream.fileno())
            raise
    return True


class DuskyTUI:
    def __init__(self, screen, windows: list[Window], target: Path):
        self.screen, self.windows, self.target = screen, windows, target
        self.index = self.offset = self.preview_offset = 0
        self.option_index = self.option_offset = 0
        self.options = False
        self.query = ''
        self.status = ''
        self.next_refresh = monotonic() + 1
        self.refresh_error = None
        self.selections: dict[str, Selection] = {}
        try:
            curses.curs_set(0)
        except curses.error:
            pass
        self.accent = curses.A_BOLD
        if curses.has_colors():
            try:
                curses.start_color()
                curses.use_default_colors()
                curses.init_pair(1, curses.COLOR_CYAN, -1)
                self.accent |= curses.color_pair(1)
            except curses.error:
                pass  # Color is optional; navigation and rendering still work.
        screen.keypad(True)
        curses.mousemask(curses.BUTTON4_PRESSED | curses.BUTTON5_PRESSED)

    @property
    def current(self) -> Window | None:
        return self.windows[self.index] if self.windows else None

    @property
    def selection(self) -> Selection:
        address = self.current.address
        if address not in self.selections:
            self.selections[address] = Selection.for_window(self.current)
        return self.selections[address]

    def put(self, y: int, x: int, text: str, attr: int = 0) -> None:
        height, width = self.screen.getmaxyx()
        if not (0 <= y < height and 0 <= x < width - 1):
            return
        # Clip by terminal cells, not code points (wide and combining titles).
        room = width - x - 1
        clipped = []
        for ch in display_text(text):
            cells = 0 if unicodedata.combining(ch) else 2 if unicodedata.east_asian_width(ch) in ('W', 'F') else 1
            if cells > room:
                break
            clipped.append(ch)
            room -= cells
        try:
            self.screen.addstr(y, x, ''.join(clipped), attr)
        except curses.error:
            pass  # Terminal may resize between getmaxyx and addstr.

    def prompt(self, label: str, initial: str = '') -> str | None:
        value = initial
        cursor = len(value)
        while True:
            height, width = self.screen.getmaxyx()
            self.screen.move(max(0, height - 3), 0)
            self.screen.clrtoeol()
            self.put(height - 3, 0, 'Arrows move  Ctrl-U clear  Enter accept  Esc cancel', curses.A_DIM)
            self.screen.move(max(0, height - 2), 0)
            self.screen.clrtoeol()
            self.put(height - 2, 0, label + ':', self.accent)
            x = min(len(label) + 2, max(0, width // 2))
            start = cursor
            cells = 1  # Leave room for the visible insertion marker.
            while start > 0:
                ch = value[start - 1]
                size = 0 if unicodedata.combining(ch) else 2 if unicodedata.east_asian_width(ch) in ('W', 'F') else 1
                if cells + size >= width - x - 1:
                    break
                cells += size
                start -= 1
            self.put(height - 2, x, value[start:cursor] + '▏' + value[cursor:])
            self.screen.refresh()
            ch = self.screen.get_wch()
            if ch in ('\n', '\r', curses.KEY_ENTER):
                return value
            if ch == '\x1b':
                return None
            if ch in ('\b', '\x7f', curses.KEY_BACKSPACE):
                if cursor:
                    value = value[:cursor - 1] + value[cursor:]
                    cursor -= 1
            elif ch == curses.KEY_DC:
                value = value[:cursor] + value[cursor + 1:]
            elif ch == curses.KEY_LEFT:
                cursor = max(0, cursor - 1)
            elif ch == curses.KEY_RIGHT:
                cursor = min(len(value), cursor + 1)
            elif ch == curses.KEY_HOME:
                cursor = 0
            elif ch == curses.KEY_END:
                cursor = len(value)
            elif ch == '\x15':
                value = ''
                cursor = 0
            elif isinstance(ch, str) and ch.isprintable():
                value = value[:cursor] + ch + value[cursor:]
                cursor += 1

    def option_rows(self) -> list[Property]:
        needle = self.query.casefold().strip()
        rows = [p for p in PROPERTIES if needle in (p.key + ' ' + p.description).casefold()]
        return sorted(rows, key=lambda p: (p.key != needle, needle not in p.key)) if needle else rows

    def set_value(self, key: str, value: str) -> None:
        candidate = Selection(set(self.selection.active), dict(self.selection.edits))
        candidate.edits[key] = value
        candidate.enable(key)
        validate_rule(generate_lua_rule(self.current, candidate))
        self.selections[self.current.address] = candidate

    def choose_preset(self, key: str, choices: list[tuple[str, str]]) -> tuple[str, str] | None:
        current = self.selection.value(self.current, key)
        index = next((i for i, (_, value) in enumerate(choices) if value == current), 0)
        offset = 0
        while True:
            self.screen.erase()
            height, width = self.screen.getmaxyx()
            self.put(0, 1, f'{key} presets', self.accent)
            if height < 12 or width < 40:
                self.put(2, 0, 'Resize to 40 x 12 or larger; Esc cancels.')
                self.screen.refresh()
                if self.screen.get_wch() in ('\x1b', 'q', 'Q'):
                    return None
                continue
            self.put(1, 1, 'Up/Down choose   Enter apply   1-9 quick pick   Esc cancel')
            count = max(1, height - 9)
            offset = max(0, min(offset, index))
            if index >= offset + count:
                offset = index - count + 1
            for i in range(offset, min(len(choices), offset + count)):
                label, value = choices[i]
                marker = '*' if value == current else ' '
                attr = curses.A_REVERSE if i == index else 0
                self.put(3 + i - offset, 1, f'{marker} {i + 1:2}. {label}', attr)
            self.put(height - 5, 1, 'Value to apply:', self.accent)
            for y, line in enumerate(textwrap.wrap(choices[index][1], max(1, width - 4)), height - 4):
                if y >= height - 1:
                    break
                self.put(y, 2, line)
            self.put(height - 1, 1, '* current value; changes apply only after choosing', curses.A_DIM)
            self.screen.refresh()
            ch = self.screen.get_wch()
            if ch == curses.KEY_MOUSE:
                _, _, _, _, buttons = curses.getmouse()
                ch = curses.KEY_UP if buttons & curses.BUTTON4_PRESSED else curses.KEY_DOWN if buttons & curses.BUTTON5_PRESSED else None
            if ch in ('\x1b', 'q', 'Q'):
                return None
            if ch in ('\n', '\r', curses.KEY_ENTER):
                return choices[index]
            if isinstance(ch, str) and ch in '123456789' and int(ch) <= len(choices):
                return choices[int(ch) - 1]
            if ch in ('j', curses.KEY_DOWN):
                index = min(len(choices) - 1, index + 1)
            elif ch in ('k', curses.KEY_UP):
                index = max(0, index - 1)
            elif ch in ('g', curses.KEY_HOME):
                index = 0
            elif ch in ('G', curses.KEY_END):
                index = len(choices) - 1
            elif ch in (curses.KEY_NPAGE, curses.KEY_PPAGE):
                index = min(len(choices) - 1, max(0, index + (count if ch == curses.KEY_NPAGE else -count)))

    def refresh(self, *, automatic: bool = False) -> None:
        self.next_refresh = monotonic() + 1
        old = self.current.address if self.current else None
        try:
            windows = scan_windows()  # Keep previous snapshot if IPC fails.
        except (OSError, ValueError, subprocess.SubprocessError) as exc:
            self.refresh_error = display_text(str(exc))
            raise
        self.windows = windows
        self.selections = {w.address: self.selections[w.address] for w in windows if w.address in self.selections}
        self.index = next((i for i, w in enumerate(windows) if w.address == old), min(self.index, max(0, len(windows) - 1)))
        if not automatic:
            self.offset = self.preview_offset = 0
            self.status = f'Refreshed: {len(windows)} native windows'
        elif self.status == self.refresh_error:
            self.status = ''
        self.refresh_error = None

    def draw(self) -> int:
        self.screen.erase()
        height, width = self.screen.getmaxyx()
        self.put(0, 1, APP_TITLE, self.accent)
        if height < 12 or width < 40:
            self.put(2, 0, 'Resize to 40 x 12 or larger. q quits.')
            self.screen.refresh()
            return 0
        self.put(height - 1, 1, self.status or str(self.target), curses.A_DIM)
        if self.options and self.current:
            rows = self.option_rows()
            self.put(1, 1, 'Options  [x] enabled  [.] commented  [ ] omitted', self.accent)
            self.put(2, 1, 'Space toggle   e custom Lua   p choose preset   / search   Esc back')
            self.option_index = min(self.option_index, max(0, len(rows) - 1))
            choices = []
            if rows and rows[self.option_index].key in VALUE_PRESETS:
                try:
                    choices = preset_choices(self.current, rows[self.option_index].key)
                except (OSError, ValueError, subprocess.SubprocessError) as exc:
                    self.status = display_text(str(exc))
                    self.put(height - 1, 1, self.status.ljust(width - 2), curses.A_DIM)
            panel_height = min(min(len(choices), 6) + 5, max(0, height - 12)) if choices else 0
            count = height - 7 - panel_height
            self.option_offset = max(0, min(self.option_offset, self.option_index))
            if self.option_index >= self.option_offset + count:
                self.option_offset = self.option_index - count + 1
            for y, p in enumerate(rows[self.option_offset:self.option_offset + count], 4):
                state = 'x' if p.key in self.selection.active else '.' if p.essential else ' '
                attr = curses.A_REVERSE if y - 4 + self.option_offset == self.option_index else 0
                self.put(y, 1, f'[{state}] {p.key:<32} {self.selection.value(self.current, p.key)}', attr)
            if choices and panel_height >= 4:
                key = rows[self.option_index].key
                current = self.selection.value(self.current, key)
                selected = next((label for label, value in choices if value == current), 'Custom Lua')
                top = 5 + min(count, len(rows) - self.option_offset)
                self.put(top, 1, f'{key} presets — number to apply, p for full picker', self.accent)
                visible = min(6, len(choices), panel_height - 1)
                for i, (label, value) in enumerate(choices[:visible], 1):
                    marker = '*' if value == current else ' '
                    self.put(top + i, 2, f'{marker} {i}. {label}')
                y = top + visible + 1
                if y < min(height - 3, top + panel_height):
                    self.put(y, 1, f'Current: {selected}', self.accent)
                for line in textwrap.wrap(current, max(1, width - 4)):
                    y += 1
                    if y >= min(height - 3, top + panel_height):
                        break
                    self.put(y, 2, line, curses.A_DIM)
            if rows:
                self.put(height - 3, 1, rows[self.option_index].description, curses.A_DIM)
            self.put(height - 2, 1, f'Search: {self.query}' if self.query else 'Enter returns to preview; save from the window screen')
            self.screen.refresh()
            return count
        self.put(1, 1, 'Enter append   c copy   o options   r refresh   q quit')
        count = min(max(2, (height - 8) // 3), max(2, len(self.windows)))
        self.offset = max(0, min(self.offset, self.index))
        if self.index >= self.offset + count:
            self.offset = self.index - count + 1
        for y, window in enumerate(self.windows[self.offset:self.offset + count], 3):
            attr = curses.A_REVERSE if y - 3 + self.offset == self.index else 0
            self.put(y, 1, f'{window.app_class or "(no class)"}  ·  {window.title}', attr)
        if not self.current:
            self.put(3, 1, 'No mapped native windows. Press r to refresh.')
        top = count + 4
        self.put(top, 1, 'Rule preview  ·  [ / ] scroll  ·  o to enable or edit properties', self.accent)
        if self.current:
            lines = generate_lua_rule(self.current, self.selection).splitlines()
            visible = height - top - 3
            self.preview_offset = min(self.preview_offset, max(0, len(lines) - visible))
            for y, line in enumerate(lines[self.preview_offset:self.preview_offset + visible], top + 1):
                self.put(y, 1, line, curses.A_DIM if line.lstrip().startswith('--') else 0)
        self.screen.refresh()
        return count

    def handle_key(self, ch, count: int) -> bool:
        if ch == curses.KEY_MOUSE:
            _, _, _, _, buttons = curses.getmouse()
            if buttons & curses.BUTTON4_PRESSED:
                ch = curses.KEY_UP
            elif buttons & curses.BUTTON5_PRESSED:
                ch = curses.KEY_DOWN
            else:
                return True
        if ch in ('q', 'Q'):
            return False
        if count == 0:
            if ch == '\x1b':
                if not self.options:
                    return False
                self.options = False
            return True
        if self.options and self.current:
            rows = self.option_rows()
            if ch in ('\x1b', '\n', '\r', 'o', curses.KEY_ENTER):
                self.options = False
            elif ch in ('j', curses.KEY_DOWN):
                self.option_index = min(max(0, len(rows) - 1), self.option_index + 1)
            elif ch in ('k', curses.KEY_UP):
                self.option_index = max(0, self.option_index - 1)
            elif ch in ('g', curses.KEY_HOME):
                self.option_index = 0
            elif ch in ('G', curses.KEY_END):
                self.option_index = max(0, len(rows) - 1)
            elif ch in (curses.KEY_NPAGE, curses.KEY_PPAGE):
                self.option_index = min(max(0, len(rows) - 1), max(0, self.option_index + (count if ch == curses.KEY_NPAGE else -count)))
            elif ch == '/':
                query = self.prompt('Search', self.query)
                if query is not None:
                    self.query, self.option_index, self.option_offset = query, 0, 0
            elif (ch in (' ', 'e', 'p') or isinstance(ch, str) and ch in '123456789') and rows:
                p = rows[self.option_index]
                if ch == 'p' or isinstance(ch, str) and ch in '123456789':
                    if p.key not in VALUE_PRESETS:
                        self.status = 'Use e for a custom value; this property has no presets'
                        return True
                    choices = preset_choices(self.current, p.key)
                    if ch == 'p':
                        choice = self.choose_preset(p.key, choices)
                        if choice is None:
                            return True
                    elif int(ch) <= len(choices):
                        choice = choices[int(ch) - 1]
                    else:
                        return True
                    label, value = choice
                    self.set_value(p.key, value)
                    self.status = f'{p.key}: {label}. e edits the value'
                elif ch == 'e':
                    value = self.prompt(p.key + ' (Lua)', self.selection.value(self.current, p.key))
                    if value is None:
                        return True
                    if not value.strip():
                        self.status = 'Value cannot be empty'
                        return True
                    self.set_value(p.key, value)
                elif p.key == 'name':
                    self.status = 'Use e to change the rule name'
                elif p.key in self.selection.active:
                    if p.key.startswith('match.') and sum(k.startswith('match.') for k in self.selection.active) == 1:
                        self.status = 'Enable another match before disabling this one'
                    else:
                        self.selection.active.remove(p.key)
                else:
                    self.selection.enable(p.key)
            return True
        if ch == '\x1b':
            return False
        if ch == 'r':
            self.refresh()
        elif self.current:
            if ch in ('j', curses.KEY_DOWN, 'k', curses.KEY_UP, curses.KEY_NPAGE, curses.KEY_PPAGE, 'g', 'G'):
                delta = count if ch == curses.KEY_NPAGE else -count if ch == curses.KEY_PPAGE else 1 if ch in ('j', curses.KEY_DOWN) else -1
                self.index = 0 if ch == 'g' else len(self.windows) - 1 if ch == 'G' else min(len(self.windows) - 1, max(0, self.index + delta))
                self.preview_offset = 0
            elif ch in ('[', ']'):
                self.preview_offset = max(0, self.preview_offset + (1 if ch == ']' else -1))
            elif ch == 'o':
                self.options = True
            elif ch in ('c', 'C', '\n', '\r', curses.KEY_ENTER):
                text = generate_lua_rule(self.current, self.selection)
                if ch in ('c', 'C'):
                    subprocess.run(['wl-copy', '--type', 'text/plain;charset=utf-8'], input=text,
                                   text=True, check=True, capture_output=True, timeout=3)
                    self.status = 'Copied rule'
                else:
                    saved = append_rule(text, self.target)
                    self.status = f'Appended to {self.target.name}' if saved else 'Identical rule already saved'
        return True

    def run(self) -> None:
        while True:
            try:
                if not self.options and monotonic() >= self.next_refresh:
                    self.refresh(automatic=True)
                count = self.draw()
                # Refresh at most once per second, regardless of key repeat.
                # Freeze the snapshot while options or an editor are open.
                self.screen.timeout(-1 if self.options else max(1, int((self.next_refresh - monotonic()) * 1000)))
                if not self.handle_key(self.screen.get_wch(), count):
                    return
            except curses.error:
                continue  # Redraw after a resize interrupts curses I/O.
            except (OSError, ValueError, subprocess.SubprocessError) as exc:
                self.status = display_text(str(exc))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument('--list', action='store_true', help='List mapped native windows')
    mode.add_argument('--print', dest='pattern', help='Print rules matching a class/title regex or exact address')
    parser.add_argument('--append', action='store_true', help='Append the rules selected with --print')
    parser.add_argument('--option', action='append', default=[], metavar='KEY=LUA', help='Enable/edit one property; repeatable (e.g. --option no_shadow=true)')
    parser.add_argument('--file', type=Path, default=TARGET_FILE, help='Destination Lua file')
    args = parser.parse_args()
    if args.append and args.pattern is None:
        parser.error('--append requires --print')
    if args.option and args.pattern is None:
        parser.error('--option requires --print; interactive mode uses o')
    options = []
    for option in args.option:
        key, sep, value = option.partition('=')
        if not sep or key not in PROPERTY_MAP or not value.strip():
            parser.error(f'Expected a known KEY=LUA, got {option!r}')
        options.append((key, value))
    try:
        windows = scan_windows()
        if args.list:
            for window in windows:
                print(f'{window.address}  {display_text(window.app_class)}  {display_text(window.title)}')
        elif args.pattern is not None:
            matches = [w for w in windows if w.address == args.pattern]
            if not matches:
                pattern = re.compile(args.pattern, re.IGNORECASE)
                matches = [w for w in windows if pattern.search(w.app_class) or pattern.search(w.title)]
            if not matches:
                raise ValueError('No windows match this pattern')
            chunks = []
            for window in matches:
                selection = Selection.for_window(window)
                for key, value in options:
                    selection.edits[key] = value
                    selection.enable(key)
                chunks.append(generate_lua_rule(window, selection))
            text = '\n'.join(chunks)
            if args.append:
                saved = append_rule(text, args.file)
                print(f'{"Appended to" if saved else "Already saved in"} {args.file}', file=sys.stderr)
            else:
                validate_rule(text)
            print(text, end='')
        else:
            if not sys.stdin.isatty() or not sys.stdout.isatty():
                raise ValueError('Interactive mode needs a terminal; use --list or --print')
            curses.wrapper(lambda screen: DuskyTUI(screen, windows, args.file).run())
        return 0
    except (OSError, ValueError, re.PatternError, subprocess.SubprocessError) as exc:
        print(f'Error: {exc}', file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        return 130


if __name__ == '__main__':
    sys.exit(main())
