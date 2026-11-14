#!/usr/bin/env python3
"""
==============================================================================
Description: Dusky Dynamic Keybinds Cheatsheet & Command Palette
Language:    Python 3.14+ / 3.15+ (Native Generics, Type Aliases, Match, IPC)
Design:      Dynamic Matugen Palette, Hybrid Hyprland Socket + Lua AST Engine
==============================================================================
"""

from __future__ import annotations

import argparse
from bisect import bisect_right
from dataclasses import asdict, dataclass, field
from enum import StrEnum
import hashlib
import json
import os
from pathlib import Path
import re
import select
import shutil
import socket
import subprocess
import sys
import tempfile
import termios
import tty
from typing import Any, Final

from rich import box
from rich.align import Align
from rich.console import Console, Group
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

# ── runtime gate ────────────────────────────────────────────────────────────
if sys.version_info < (3, 14):
    sys.exit(
        f"│ FATAL │ Python 3.14+ required  ·  running {sys.version.split()[0]}"
    )

# ── paths & constants ───────────────────────────────────────────────────────
type KeyTuple = tuple[str, ...]

_XDG_CONFIG: Final[Path] = Path(
    os.environ.get("XDG_CONFIG_HOME") or (Path.home() / ".config")
)
CONFIG_ROOT: Final[Path] = (
    _XDG_CONFIG if _XDG_CONFIG.is_absolute() else (Path.home() / ".config")
)
_XDG_CACHE: Final[Path] = Path(
    os.environ.get("XDG_CACHE_HOME") or (Path.home() / ".cache")
)
CACHE_DIR: Final[Path] = (
    _XDG_CACHE if _XDG_CACHE.is_absolute() else (Path.home() / ".cache")
) / "dusky"
CACHE_FILE: Final[Path] = CACHE_DIR / "keybinds_cache.json"
CACHE_SCHEMA_VERSION: Final[str] = "dusky-cheatsheet-v3"

SOURCE_LUA: Final[Path] = CONFIG_ROOT / "hypr/source/keybinds.lua"
CUSTOM_LUA: Final[Path] = CONFIG_ROOT / "hypr/edit_here/source/keybinds.lua"
MATUGEN_PATH: Final[Path] = CONFIG_ROOT / "matugen/generated/dusky_tui.json"

MAX_CHORD_WIDTH: Final[int] = 21
DEFAULT_CARD_LIMIT: Final[int] = 9


# ── Matugen Dynamic Palette Integration ─────────────────────────────────────
class Palette:
    BG: str      = "#1d100a"
    FG: str      = "#f8ddd2"
    ACCENT: str  = "#ffb694"
    ERROR: str   = "#ffb4ab"
    WARNING: str = "#efbc94"
    SUCCESS: str = "#f0be79"
    MUTED: str   = "#c8b0a6"

    @classmethod
    def load(cls) -> None:
        if MATUGEN_PATH.exists():
            try:
                data = json.loads(MATUGEN_PATH.read_text(encoding="utf-8"))
                cls.BG = data.get("bg", cls.BG)
                cls.FG = data.get("fg", cls.FG)
                cls.ACCENT = data.get("accent", cls.ACCENT)
                cls.ERROR = data.get("error", cls.ERROR)
                cls.WARNING = data.get("warning", cls.WARNING)
                cls.SUCCESS = data.get("success", cls.SUCCESS)
                cls.MUTED = data.get("muted", cls.FG)
            except Exception:
                pass


Palette.load()
C = Palette


# ── domain models ───────────────────────────────────────────────────────────
class Tag(StrEnum):
    ACCENT  = "accent"
    SUCCESS = "success"
    WARNING = "warning"
    DANGER  = "danger"
    MUTED   = "muted"


class CategoryId(StrEnum):
    LAUNCHERS  = "LAUNCHERS  &  SEARCH"
    WINDOWS    = "WINDOW  TILING  &  POSITION"
    WORKSPACES = "WORKSPACES  &  NAVIGATION"
    SYSTEM     = "SYSTEM  ·  TOOLS  ·  CONTROLS"


def tag_fg(tag: Tag) -> str:
    """Map semantic tag to Matugen foreground hex colour."""
    match tag:
        case Tag.ACCENT:  return C.ACCENT
        case Tag.SUCCESS: return C.SUCCESS
        case Tag.WARNING: return C.WARNING
        case Tag.DANGER:  return C.ERROR
        case Tag.MUTED:   return C.FG


def category_meta(cat_id: CategoryId) -> tuple[str, str]:
    """Return (icon, accent_color) for each of the 4 canonical categories."""
    match cat_id:
        case CategoryId.LAUNCHERS:  return ("󰀻", C.ACCENT)
        case CategoryId.WINDOWS:    return ("󰝣", C.SUCCESS)
        case CategoryId.WORKSPACES: return ("󰽙", C.WARNING)
        case CategoryId.SYSTEM:     return ("󰒓", C.ERROR)


MODS: Final[frozenset[str]] = frozenset({
    "SUPER", "ALT", "CTRL", "SHIFT", "MOUSE", "DRAG", "SCROLL", "PRINT",
})

MODMASK_BITS: Final[tuple[tuple[int, str], ...]] = (
    (64, "SUPER"),
    (4, "CTRL"),
    (8, "ALT"),
    (1, "SHIFT"),
)


@dataclass(frozen=True, slots=True)
class Bind:
    keys: KeyTuple
    action: str
    tag: Tag = Tag.ACCENT
    dispatcher: str = ""
    arg: str = ""
    submap: str = ""
    origin: str = "SRC"
    priority: int = 50


@dataclass(frozen=True, slots=True)
class Category:
    title: str
    icon: str
    accent: str
    binds: tuple[Bind, ...]


@dataclass(frozen=True, slots=True)
class RawHyprBind:
    modmask: int
    mods: KeyTuple
    key: str
    keycode: int
    submap: str
    description: str
    dispatcher: str
    arg: str
    mouse: bool = False
    origin: str = "HYPR"
    cat_hint: str = ""
    tag_hint: str = ""


@dataclass(frozen=True, slots=True)
class LuaToken:
    value: str
    start: int
    end: int
    string: bool = False


@dataclass(frozen=True, slots=True)
class LuaAstBind:
    modmask: int
    mods: KeyTuple
    key: str
    dispatcher_raw: str
    dispatcher_kind: str
    dispatcher_arg: str
    description: str
    submap: str
    origin: str
    is_unbind: bool
    dynamic: bool
    cat_hint: str = ""
    tag_hint: str = ""
    line: int = 0


# ── fallback static catalogue (used only if Hyprland & Lua files unavailable) ─
FALLBACK_CATALOGUE: Final[tuple[Category, ...]] = (
    Category(
        title=CategoryId.LAUNCHERS.value,
        icon="󰀻",
        accent=C.ACCENT,
        binds=(
            Bind(("SUPER", "Q"),             "Launch Terminal",         Tag.ACCENT),
            Bind(("SUPER", "W"),             "Launch Web Browser",      Tag.ACCENT),
            Bind(("SUPER", "E"),             "Launch File Manager",     Tag.ACCENT),
            Bind(("SUPER", "R"),             "Open Text Editor",        Tag.ACCENT),
            Bind(("ALT", "SPACE"),           "App Launcher & Search",   Tag.SUCCESS),
            Bind(("SUPER", "SPACE"),         "System Quick Menu",       Tag.SUCCESS),
            Bind(("SUPER", "V"),             "Clipboard Manager",       Tag.ACCENT),
            Bind(("SUPER", "G"),             "Image Search & Lens",     Tag.SUCCESS),
            Bind(("SUPER", "CTRL", "SPACE"), "Emoji Picker & Insert",   Tag.SUCCESS),
        ),
    ),
    Category(
        title=CategoryId.WINDOWS.value,
        icon="󰝣",
        accent=C.SUCCESS,
        binds=(
            Bind(("SUPER", "C"),                "Close Focused Window",   Tag.DANGER),
            Bind(("SUPER", "A"),                "Toggle Fullscreen",      Tag.WARNING),
            Bind(("SUPER", "D"),                "Toggle Smart Float",     Tag.ACCENT),
            Bind(("SUPER", "Y"),                "Toggle Window Split",    Tag.ACCENT),
            Bind(("SUPER", "X"),                "Pin Window (Sticky)",    Tag.ACCENT),
            Bind(("SUPER", "SHIFT", "A"),       "Maximize Window",        Tag.WARNING),
            Bind(("SUPER", "H/J/K/L"),          "Focus Directionally",    Tag.MUTED),
            Bind(("SUPER", "SHIFT", "H/J/K/L"), "Move Window Position",   Tag.MUTED),
            Bind(("SUPER", "DRAG"),             "Move / Resize Window",   Tag.MUTED),
        ),
    ),
    Category(
        title=CategoryId.WORKSPACES.value,
        icon="󰽙",
        accent=C.WARNING,
        binds=(
            Bind(("SUPER", "1..9"),            "Switch Workspace 1–9",     Tag.ACCENT),
            Bind(("SUPER", "SHIFT", "1..9"),   "Move Window to WS 1–9",    Tag.WARNING),
            Bind(("SUPER", "ALT", "1..9"),     "Silent Move Window to WS", Tag.MUTED),
            Bind(("SUPER", "TAB"),             "Last Active Workspace",    Tag.SUCCESS),
            Bind(("SUPER", "Z"),               "Toggle Scratchpad",        Tag.SUCCESS),
            Bind(("SUPER", "SHIFT", "Z"),      "Send to Scratchpad",       Tag.WARNING),
            Bind(("SUPER", "SHIFT", "M"),      "Special Spotify WS",       Tag.ACCENT),
            Bind(("SUPER", "SCROLL"),          "Cycle Workspaces",         Tag.MUTED),
            Bind(("SUPER", "SHIFT", "TAB"),    "Cycle Next Workspace",     Tag.SUCCESS),
        ),
    ),
    Category(
        title=CategoryId.SYSTEM.value,
        icon="󰒓",
        accent=C.ERROR,
        binds=(
            Bind(("SUPER", "M"),             "Lock Screen",             Tag.DANGER),
            Bind(("ALT", "F4"),              "Power & Logout Menu",     Tag.DANGER),
            Bind(("CTRL", "SHIFT", "ESC"),   "Activity Monitor",        Tag.ACCENT),
            Bind(("SUPER", "S"),             "Quick Screenshot",        Tag.SUCCESS),
            Bind(("SUPER", "SHIFT", "S"),    "Screenshot & Annotate",   Tag.SUCCESS),
            Bind(("SUPER", "B"),             "Color Picker",            Tag.ACCENT),
            Bind(("SUPER", "ALT", "O"),      "AI Ollama Chat TUI",      Tag.ACCENT),
            Bind(("ALT", "1 / 2 / 3"),       "Wi-Fi / BT / Audio TUI",  Tag.MUTED),
            Bind(("SUPER", "SHIFT", "R"),    "Reload Hyprland Config",  Tag.DANGER),
        ),
    ),
)


# ── Lua lexical tokenizer & static AST extractor ────────────────────────────
_LUA_SPACE: Final[str] = " \f\n\r\t\v"
_STRING_ESCAPES: Final[dict[str, str]] = {
    "a": "\a", "b": "\b", "f": "\f", "n": "\n", "r": "\r", "t": "\t", "v": "\v"
}
_NEWLINE: Final[re.Pattern[str]] = re.compile(r"\r\n|\n\r|\r|\n")
_LONG_OPEN: Final[re.Pattern[str]] = re.compile(r"\[(=*)\[")
_LEX: Final[re.Pattern[str]] = re.compile(
    r"(?P<space>\s+)|(?P<comment>--)|(?P<long>\[(=*)\[)|"
    r'''(?P<quoted>"(?:\\(?:z\s*+|\r\n|\n\r|[\s\S])|[^"\\\r\n]++)*+"|'(?:\\(?:z\s*+|\r\n|\n\r|[\s\S])|[^'\\\r\n]++)*+')|'''
    r"[A-Za-z_][A-Za-z_0-9]*|\.\.\.|\.\.|==|~=|<=|>=|::|[^\s]",
    re.ASCII,
)
_CAT_ANNOTATION_RE: Final[re.Pattern[str]] = re.compile(
    r"@cat(?:egory)?\s*[:=]\s*([A-Za-z0-9_ &·.-]+)", re.IGNORECASE
)
_TAG_ANNOTATION_RE: Final[re.Pattern[str]] = re.compile(
    r"@tag\s*[:=]\s*(accent|success|warning|danger|muted)", re.IGNORECASE
)
_EXEC_CMD_RE: Final[re.Pattern[str]] = re.compile(
    r"^hl\.dsp\.exec_cmd\(\s*([\"'].*[\"'])\s*\)$", re.DOTALL
)
_MOD_NAME_BITS: Final[dict[str, int]] = {
    "SHIFT": 1,
    "CTRL": 4,
    "CONTROL": 4,
    "ALT": 8,
    "MOD1": 8,
    "SUPER": 64,
    "WIN": 64,
    "LOGO": 64,
    "MOD4": 64,
}


def decode_modmask(mask: int) -> KeyTuple:
    return tuple(name for bit, name in MODMASK_BITS if mask & bit)


def split_lua_key_string(keys_str: str) -> tuple[int, KeyTuple, str]:
    modmask = 0
    key = ""
    for part in keys_str.split("+"):
        tok = part.strip()
        if not tok:
            continue
        bit = _MOD_NAME_BITS.get(tok.upper())
        if bit is not None:
            modmask |= bit
        else:
            key = tok
    return modmask, decode_modmask(modmask), key


def lua_tokens(code: str) -> list[LuaToken]:
    """Tokenize Lua source without executing code."""
    result: list[LuaToken] = []
    i, n = 0, len(code)
    while i < n:
        match = _LEX.prefixmatch(code, i)
        if match is None:
            i += 1
            continue
        start, i = i, match.end()
        kind = match.lastgroup
        if kind == "space":
            continue
        if kind in ("comment", "long"):
            opener = _LONG_OPEN.prefixmatch(code, i if kind == "comment" else start)
            if opener:
                close = "]" + opener[1] + "]"
                end = code.find(close, opener.end())
                if end < 0:
                    break
                i = end + len(close)
            else:
                end_match = _NEWLINE.search(code, i)
                i = n if end_match is None else end_match.start()
            if kind == "comment":
                continue
        elif code[start] in "\"'" and kind != "quoted":
            break
        result.append(LuaToken(code[start:i], start, i, kind in ("quoted", "long")))
    return result


def lua_string_bytes(value: str) -> bytes:
    match = _LONG_OPEN.prefixmatch(value)
    if match:
        body = value[match.end() : -(len(match[1]) + 2)]
        body = _NEWLINE.sub("\n", body)
        return (body[1:] if body.startswith("\n") else body).encode("utf-8")
    body = value[1:-1]
    if "\\" not in body:
        return body.encode("utf-8")
    out = bytearray()
    i = 0
    while i < len(body):
        if body[i] != "\\":
            out.extend(body[i].encode("utf-8"))
            i += 1
            continue
        i += 1
        if i >= len(body):
            break
        char = body[i]
        if char == "z":
            i += 1
            while i < len(body) and body[i] in _LUA_SPACE:
                i += 1
        elif char == "x" and i + 2 < len(body):
            try:
                out.append(int(body[i + 1 : i + 3], 16))
                i += 3
            except ValueError:
                i += 1
        elif char == "u" and body[i + 1 : i + 2] == "{":
            end = body.find("}", i + 2)
            if end < 0:
                break
            point = int(body[i + 2 : end], 16)
            if 0 <= point <= 0x10FFFF and not (0xD800 <= point <= 0xDFFF):
                out.extend(chr(point).encode("utf-8"))
            i = end + 1
        elif char.isascii() and char.isdigit():
            m = re.prefixmatch(r"[0-9]{1,3}", body[i:])
            assert m is not None
            out.append(int(m[0]) & 0xFF)
            i += len(m[0])
        elif char in "\r\n":
            out.append(10)
            i += 2 if body[i : i + 2] in ("\r\n", "\n\r") else 1
        else:
            out.extend(_STRING_ESCAPES.get(char, char).encode("utf-8"))
            i += 1
    return bytes(out)


def literal_string(tokens: list[LuaToken]) -> str | None:
    if not tokens or len(tokens) % 2 == 0:
        return None
    if any(not t.string for t in tokens[::2]) or any(t.value != ".." for t in tokens[1::2]):
        return None
    return b"".join(lua_string_bytes(t.value) for t in tokens[::2]).decode("utf-8", errors="replace")


def call_arguments(
    tokens: list[LuaToken],
    open_idx: int,
    separators: tuple[str, ...] = (",",),
) -> tuple[list[list[LuaToken]], int]:
    brackets = [")"]
    blocks: list[str] = []
    args: list[list[LuaToken]] = []
    start = open_idx + 1
    for i in range(start, len(tokens)):
        token = tokens[i]
        if token.string:
            continue
        value = token.value
        if value in ("function", "if", "for", "while", "repeat"):
            blocks.append(value)
        elif value == "do":
            if blocks and blocks[-1] in ("for", "while"):
                blocks[-1] = "loop"
            else:
                blocks.append("do")
        elif value in ("end", "until"):
            if blocks:
                blocks.pop()
        elif value in ("(", "[", "{"):
            brackets.append({"(": ")", "[": "]", "{": "}"}[value])
        elif value in (")", "]", "}"):
            if not brackets or value != brackets.pop():
                break
            if not brackets:
                if start < i:
                    args.append(tokens[start:i])
                return args, i
        elif value in separators and len(brackets) == 1 and not blocks:
            args.append(tokens[start:i])
            start = i + 1
    return args, len(tokens) - 1


def token_text(text: str, tokens: list[LuaToken]) -> str:
    return text[tokens[0].start : tokens[-1].end] if tokens else ""


def parse_lua_dispatcher(raw_dsp: str) -> tuple[str, str]:
    """Extract executable dispatcher and arg from Lua AST expression."""
    trimmed = raw_dsp.strip()
    if m := _EXEC_CMD_RE.match(trimmed):
        inner_tokens = lua_tokens(m.group(1))
        cmd = literal_string(inner_tokens)
        if cmd:
            return ("exec", cmd)
    if trimmed.startswith("hl.dsp.") and "(" in trimmed and trimmed.endswith(")"):
        name_part, _, rest = trimmed[7:-1].partition("(")
        inner_tokens = lua_tokens(rest)
        arg_str = literal_string(inner_tokens) if inner_tokens else ""
        return (name_part.strip(), arg_str or rest.strip().strip("\"'"))
    return ("lua", trimmed)


def extract_comment_annotations(lines: list[str], line_idx: int, active_cat: str) -> tuple[str, str]:
    """Extract `-- @cat: ...` and `-- @tag: ...` from current or preceding line."""
    cat_hint = active_cat
    tag_hint = ""
    search_Indices = [max(0, line_idx - 2), line_idx - 1] if line_idx >= 1 else []
    for idx in search_Indices:
        if 0 <= idx < len(lines):
            ln = lines[idx]
            if m_cat := _CAT_ANNOTATION_RE.search(ln):
                cat_hint = m_cat.group(1).strip()
            if m_tag := _TAG_ANNOTATION_RE.search(ln):
                tag_hint = m_tag.group(1).strip().lower()
    return cat_hint, tag_hint


def parse_lua_binds(path: Path, origin: str) -> list[LuaAstBind]:
    try:
        text = path.read_text(encoding="utf-8", newline="")
    except OSError:
        return []
    if not text.strip():
        return []

    tokens = lua_tokens(text)
    newlines = [m.start() for m in _NEWLINE.finditer(text)]
    lines = text.splitlines()

    # Scan section comments `-- @cat: ...` by line number
    section_cats: list[tuple[int, str]] = []
    for idx, ln in enumerate(lines, 1):
        if m := _CAT_ANNOTATION_RE.search(ln):
            section_cats.append((idx, m.group(1).strip()))

    stack: list[tuple[str, int, bool]] = []
    wrapper_regions: list[tuple[int, int]] = []
    for i, token in enumerate(tokens):
        if token.string:
            continue
        val = token.value
        if val in ("function", "if", "for", "while", "repeat"):
            is_wrapper = (
                val == "function"
                and i + 1 < len(tokens)
                and tokens[i + 1].value == "cond_bind"
            )
            stack.append((val, token.start, is_wrapper))
        elif val == "do":
            if stack and stack[-1][0] in ("for", "while"):
                _, start, is_wrapper = stack.pop()
                stack.append(("loop", start, is_wrapper))
            else:
                stack.append(("do", token.start, False))
        elif val in ("end", "until") and stack:
            _, start, is_wrapper = stack.pop()
            if is_wrapper:
                wrapper_regions.append((start, token.end))

    regions: list[tuple[int, int, str]] = []
    calls: list[tuple[LuaToken, str, list[list[LuaToken]], int]] = []

    for i, token in enumerate(tokens):
        if token.string or i + 1 >= len(tokens):
            continue
        if any(start < token.start < end for start, end in wrapper_regions):
            continue
        if token.value == "cond_bind" and tokens[i + 1].value == "(":
            if i and tokens[i - 1].value in ("function", ".", ":"):
                continue
            name, op = "bind", i + 1
        elif (
            token.value == "hl"
            and i + 3 < len(tokens)
            and tokens[i + 1].value == "."
            and tokens[i + 3].value == "("
            and tokens[i + 2].value in ("bind", "unbind", "define_submap")
            and (not i or tokens[i - 1].value not in (".", ":"))
        ):
            name, op = tokens[i + 2].value, i + 3
        else:
            continue

        args, end_idx = call_arguments(tokens, op)
        if name == "define_submap":
            if args and args[0]:
                sub_name = literal_string(args[0]) or f"<{token_text(text, args[0])}>"
                regions.append((token.start, tokens[end_idx].end, sub_name))
        else:
            calls.append((token, name, args, end_idx))

    ast_binds: list[LuaAstBind] = []
    for token, name, args, _ in calls:
        if not args or (name == "bind" and len(args) < 2):
            continue
        key_lit = literal_string(args[0])
        is_dynamic = key_lit is None
        key_str = key_lit if key_lit is not None else token_text(text, args[0])
        modmask, mods, bare_key = (
            split_lua_key_string(key_str) if not is_dynamic else (0, (), key_str)
        )

        desc = ""
        if len(args) > 2 and args[2] and args[2][0].value == "{" and args[2][-1].value == "}":
            fields, _ = call_arguments(
                [LuaToken("(", 0, 0), *args[2][1:-1], LuaToken(")", 0, 0)],
                0,
                (",", ";"),
            )
            for fld in fields:
                if len(fld) >= 3 and fld[0].value == "description" and fld[1].value == "=":
                    desc = literal_string(fld[2:]) or ""

        enclosing = [(s, e, sub) for s, e, sub in regions if s < token.start < e]
        submap = min(enclosing, key=lambda r: r[1] - r[0])[2] if enclosing else ""
        line_no = bisect_right(newlines, token.start) + 1

        active_cat = ""
        for sec_line, sec_cat in section_cats:
            if sec_line <= line_no:
                active_cat = sec_cat
            else:
                break
        cat_hint, tag_hint = extract_comment_annotations(lines, line_no, active_cat)

        dsp_raw = token_text(text, args[1]) if len(args) > 1 else "UNBIND"
        dsp_kind, dsp_arg = parse_lua_dispatcher(dsp_raw) if name == "bind" else ("unbind", "")

        ast_binds.append(
            LuaAstBind(
                modmask=modmask,
                mods=mods,
                key=bare_key,
                dispatcher_raw=dsp_raw,
                dispatcher_kind=dsp_kind,
                dispatcher_arg=dsp_arg,
                description=desc,
                submap=submap,
                origin=origin,
                is_unbind=(name == "unbind"),
                dynamic=is_dynamic,
                cat_hint=cat_hint,
                tag_hint=tag_hint,
                line=line_no,
            )
        )
    return ast_binds


# ── Hyprland IPC Socket & XKB Keycode Resolution ────────────────────────────
def hypr_socket_path() -> Path | None:
    his = os.environ.get("HYPRLAND_INSTANCE_SIGNATURE")
    if not his:
        return None
    runtime = os.environ.get("XDG_RUNTIME_DIR")
    candidates = []
    if runtime:
        candidates.append(Path(runtime) / "hypr" / his / ".socket.sock")
    candidates.append(Path("/tmp/hypr") / his / ".socket.sock")
    for path in candidates:
        if path.exists():
            return path
    return None


def hypr_ipc(command: str) -> str:
    """Query Hyprland UNIX domain socket in <0.5ms with subprocess fallback."""
    sock_path = hypr_socket_path()
    if sock_path is not None:
        try:
            with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as sock:
                sock.settimeout(0.75)
                sock.connect(str(sock_path))
                sock.sendall(command.encode("utf-8"))
                chunks: list[bytes] = []
                while chunk := sock.recv(65536):
                    chunks.append(chunk)
            return b"".join(chunks).decode("utf-8", errors="replace")
        except OSError:
            pass

    if not shutil.which("hyprctl"):
        return ""
    try:
        args = ["hyprctl", *command.split()]
        res = subprocess.run(args, capture_output=True, text=True, timeout=1.5)
        return res.stdout if res.returncode == 0 else ""
    except (OSError, subprocess.SubprocessError):
        return ""


def get_leading_keyboard() -> tuple[str, str]:
    """Fetch leading XKB layout and variant configured in Hyprland."""
    out_vals: list[str] = []
    for opt in ("kb_layout", "kb_variant"):
        raw = hypr_ipc(f"j/getoption input:{opt}")
        val = ""
        if raw:
            try:
                val = str(json.loads(raw).get("str", "")).split(",")[0].strip()
            except Exception:
                val = ""
        if not re.fullmatch(r"[A-Za-z0-9_-]*", val):
            val = ""
        out_vals.append(val)
    return out_vals[0], out_vals[1]


_XKB_CODE_RE: Final[re.Pattern[str]] = re.compile(r"<([A-Za-z0-9_]+)>\s*=\s*([0-9]+)\s*;")
_XKB_SYM_RE: Final[re.Pattern[str]] = re.compile(
    r"key\s*<([A-Za-z0-9_]+)>\s*\{\s*\[\s*([^,\s\]]+)"
)

KEY_ALIASES: Final[dict[str, str]] = {
    "GRAVE": "~",
    "ASCIICIRCUM": "^",
    "COMMA": ",",
    "PERIOD": ".",
    "MINUS": "-",
    "EQUAL": "=",
    "SLASH": "/",
    "BACKSLASH": "\\",
    "BRACKETLEFT": "[",
    "BRACKETRIGHT": "]",
    "SEMICOLON": ";",
    "APOSTROPHE": "'",
    "RETURN": "ENTER",
    "ESCAPE": "ESC",
    "BACKSPACE": "BKSP",
    "DELETE": "DEL",
    "PRIOR": "PGUP",
    "NEXT": "PGDN",
    "MOUSE:272": "DRAG",
    "MOUSE:273": "DRAG",
    "MOUSE:274": "MMB",
    "MOUSE_DOWN": "SCROLL",
    "MOUSE_UP": "SCROLL",
    "MOUSE_LEFT": "SCROLL",
    "MOUSE_RIGHT": "SCROLL",
    "XF86AUDIORAISEVOLUME": "VOL+",
    "XF86AUDIOLOWERVOLUME": "VOL-",
    "XF86AUDIOMUTE": "MUTE",
    "XF86AUDIOMICMUTE": "MICMUTE",
    "XF86MONBRIGHTNESSUP": "BRI+",
    "XF86MONBRIGHTNESSDOWN": "BRI-",
    "XF86POWEROFF": "POWER",
}


def build_keycode_map(layout: str, variant: str) -> dict[str, str]:
    """Compile XKB keymap to map `code:XX` to physical layout symbols."""
    mapping: dict[str, str] = {
        "10": "1", "11": "2", "12": "3", "13": "4", "14": "5",
        "15": "6", "16": "7", "17": "8", "18": "9", "19": "0",
        "20": "-", "21": "=", "59": ",", "60": ".", "61": "/",
    }
    if not shutil.which("xkbcli"):
        return mapping

    cmd = ["xkbcli", "compile-keymap"]
    if layout:
        cmd.extend(["--layout", layout])
    if layout and variant:
        cmd.extend(["--variant", variant])

    try:
        proc = subprocess.run(
            cmd, stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=1.5
        )
        if proc.returncode != 0:
            return mapping
    except (OSError, subprocess.SubprocessError):
        return mapping

    code_by_name: dict[str, str] = {}
    sym_by_name: dict[str, str] = {}
    section = ""
    for line in proc.stdout.splitlines():
        if "xkb_keycodes" in line:
            section = "codes"
        elif "xkb_symbols" in line:
            section = "syms"
        elif section == "codes" and (m := _XKB_CODE_RE.search(line)):
            code_by_name[m[1]] = m[2]
        elif section == "syms" and (m := _XKB_SYM_RE.search(line)):
            sym_by_name[m[1]] = m[2]

    for name, code in code_by_name.items():
        sym = sym_by_name.get(name, "")
        if sym and sym != "NoSymbol":
            up = sym.upper()
            mapping[code] = KEY_ALIASES.get(up, up)
    return mapping


def normalize_key_token(raw_key: str, keycode_map: dict[str, str]) -> str:
    clean = raw_key.strip()
    if clean.startswith("code:"):
        code = clean.removeprefix("code:")
        return keycode_map.get(code, clean)
    up = clean.upper()
    return KEY_ALIASES.get(up, up)


# ── Live `binds` Parser & Hybrid Merger ─────────────────────────────────────
def parse_live_hypr_binds(
    raw_binds_text: str,
    lua_binds: list[LuaAstBind],
) -> list[RawHyprBind]:
    """
    Parse plain `hyprctl binds` output using a line state machine (immune to
    Hyprland 0.56 JSON bugs) and enrich `__lua` binds from the Lua AST.
    """
    # Build fast lookup maps from Lua AST (later entries like CUST override SRC)
    by_mod_desc: dict[tuple[int, str], LuaAstBind] = {}
    by_mod_key: dict[tuple[int, str], LuaAstBind] = {}
    for lb in lua_binds:
        if lb.is_unbind:
            continue
        if lb.description:
            by_mod_desc[(lb.modmask, lb.description)] = lb
        if lb.key and not lb.dynamic:
            by_mod_key[(lb.modmask, lb.key.upper())] = lb

    records: list[dict[str, str]] = []
    current: dict[str, str] | None = None

    for line in raw_binds_text.splitlines():
        if line.startswith("bind"):
            if current is not None:
                records.append(current)
            current = {}
        elif current is not None and line.startswith("\t") and ": " in line:
            k, _, v = line[1:].partition(": ")
            current[k.strip()] = v.strip()
    if current is not None:
        records.append(current)

    result: list[RawHyprBind] = []
    for f in records:
        modmask = int(f.get("modmask", "0") or 0)
        keycode = int(f.get("keycode", "0") or 0)
        raw_key = f.get("key", "")
        if " + " in raw_key:
            raw_key = raw_key.rsplit(" + ", 1)[-1].strip()
        if not raw_key and keycode != 0:
            raw_key = f"code:{keycode}"

        description = f.get("description", "")
        dispatcher = f.get("dispatcher", "")
        arg = f.get("arg", "")
        submap = f.get("submap", "")
        is_mouse = f.get("mouse", "0") == "1"

        # Match against Lua AST for origin, missing keys, __lua dispatchers, and @cat/@tag
        matched_lua = (
            by_mod_desc.get((modmask, description))
            if description
            else None
        ) or by_mod_key.get((modmask, raw_key.upper()))

        origin = "SRC"
        cat_hint = ""
        tag_hint = ""
        if matched_lua is not None:
            origin = matched_lua.origin
            cat_hint = matched_lua.cat_hint
            tag_hint = matched_lua.tag_hint
            if not raw_key and matched_lua.key:
                raw_key = matched_lua.key
            if not description and matched_lua.description:
                description = matched_lua.description
            if dispatcher == "__lua":
                dispatcher = matched_lua.dispatcher_kind
                arg = matched_lua.dispatcher_arg

        # Skip internal descriptionless __lua hooks or duplicate Copilot key
        if (not description and dispatcher in ("__lua", "lua")) or raw_key == "code:201":
            continue
        if raw_key.startswith("switch:"):
            continue

        result.append(
            RawHyprBind(
                modmask=modmask,
                mods=decode_modmask(modmask),
                key=raw_key,
                keycode=keycode,
                submap=submap,
                description=description,
                dispatcher=dispatcher,
                arg=arg,
                mouse=is_mouse,
                origin=origin,
                cat_hint=cat_hint,
                tag_hint=tag_hint,
            )
        )
    return result


def fallback_binds_from_ast(lua_binds: list[LuaAstBind]) -> list[RawHyprBind]:
    """Convert static Lua AST declarations into RawHyprBinds when offline."""
    active: dict[tuple[int, str, str], RawHyprBind] = {}
    for lb in lua_binds:
        if lb.dynamic:
            continue
        sig = (lb.modmask, lb.key.upper(), lb.submap)
        if lb.is_unbind:
            active.pop(sig, None)
            continue
        if not lb.description and lb.dispatcher_kind == "lua":
            continue
        active[sig] = RawHyprBind(
            modmask=lb.modmask,
            mods=lb.mods,
            key=lb.key,
            keycode=0,
            submap=lb.submap,
            description=lb.description,
            dispatcher=lb.dispatcher_kind,
            arg=lb.dispatcher_arg,
            mouse=lb.key.lower().startswith("mouse"),
            origin=lb.origin,
            cat_hint=lb.cat_hint,
            tag_hint=lb.tag_hint,
        )
    return list(active.values())


# ── Heuristic Classifier, Tag Inference & Priority Scoring ──────────────────
_PREFIX_CAT_RE: Final[re.Pattern[str]] = re.compile(
    r"^\[?\s*(launch(?:ers?)?|search|apps?|windows?|tiling|workspaces?|ws|nav(?:igation)?|sys(?:tem)?|tools?|controls?|media)\s*[\]:-]\s*(.+)$",
    re.IGNORECASE,
)


def resolve_category_and_clean_action(
    action: str,
    dispatcher: str,
    arg: str,
    keys: KeyTuple,
    cat_hint: str,
) -> tuple[CategoryId, str]:
    """Classify a bind into one of the 4 canonical Dusky categories."""
    cleaned_action = action.strip()
    hint = cat_hint.strip().upper()

    if m := _PREFIX_CAT_RE.match(cleaned_action):
        hint = m.group(1).upper()
        cleaned_action = m.group(2).strip()

    if hint:
        if any(k in hint for k in ("LAUNCH", "SEARCH", "APP")):
            return CategoryId.LAUNCHERS, cleaned_action
        if any(k in hint for k in ("WIN", "TIL", "POS", "FLOAT")):
            return CategoryId.WINDOWS, cleaned_action
        if any(k in hint for k in ("WORK", "WS", "NAV", "SCRATCH")):
            return CategoryId.WORKSPACES, cleaned_action
        if any(k in hint for k in ("SYS", "TOOL", "CTRL", "CONTROL", "MEDIA")):
            return CategoryId.SYSTEM, cleaned_action

    haystack = f"{cleaned_action} {dispatcher} {arg}".lower()
    dsp = dispatcher.lower()

    # 1. Workspaces & Navigation
    if (
        any(
            d in dsp
            for d in (
                "workspace",
                "movetoworkspace",
                "movetoworkspacesilent",
                "togglespecialworkspace",
                "focusworkspaceoncurrentmonitor",
            )
        )
        or any(
            w in haystack
            for w in (
                "workspace",
                "scratchpad",
                "special ws",
                "spotify ws",
                "cycle ws",
                "last active workspace",
            )
        )
        or "1..9" in keys
        or "SCROLL" in keys
    ):
        return CategoryId.WORKSPACES, cleaned_action

    # 2. Window Tiling & Position
    if (
        any(
            d in dsp
            for d in (
                "killactive",
                "forcekillactive",
                "closewindow",
                "fullscreen",
                "togglefloating",
                "togglesplit",
                "pseudo",
                "pin",
                "movefocus",
                "movewindow",
                "swapwindow",
                "resizewindow",
                "resizeactive",
                "centerwindow",
                "cyclenext",
                "alterzorder",
            )
        )
        or any(
            w in haystack
            for w in (
                "window",
                "fullscreen",
                "maximize",
                "float",
                "split",
                "sticky",
                "pin ",
                "focus direction",
                "move / resize",
                "tile",
            )
        )
        or "H/J/K/L" in keys
        or "←/↓/↑/→" in keys
        or "DRAG" in keys
    ):
        return CategoryId.WINDOWS, cleaned_action

    # 3. Launchers & Search
    if any(
        w in haystack
        for w in (
            "terminal",
            "browser",
            "file manager",
            "text editor",
            "editor",
            "launcher",
            "rofi",
            "fuzzel",
            "walker",
            "wofi",
            "tofi",
            "quick menu",
            "omarchy menu",
            "clipboard",
            "emoji",
            "lens",
            "image search",
            "launch ",
            "open ",
            "calculator",
            "webapp",
        )
    ) and not any(
        s in haystack
        for s in ("screenshot", "lock", "logout", "power", "reload", "color picker", "ai ", "ollama", "btop", "activity", "wifi", "bluetooth", "audio")
    ):
        return CategoryId.LAUNCHERS, cleaned_action

    # 4. System · Tools · Controls
    return CategoryId.SYSTEM, cleaned_action


def infer_tag(
    cat_id: CategoryId,
    action: str,
    dispatcher: str,
    arg: str,
    keys: KeyTuple,
    tag_hint: str,
) -> Tag:
    """Assign semantic Matugen color tag."""
    if tag_hint:
        try:
            return Tag(tag_hint.lower())
        except ValueError:
            pass

    haystack = f"{action} {dispatcher} {arg}".lower()

    if any(
        w in haystack
        for w in (
            "close",
            "kill",
            "lock",
            "power",
            "logout",
            "exit",
            "reboot",
            "shutdown",
            "reload",
        )
    ):
        return Tag.DANGER

    if any(
        w in haystack
        for w in (
            "fullscreen",
            "maximize",
            "move window to ws",
            "movetoworkspace ",
            "send to scratchpad",
            "resize",
        )
    ):
        return Tag.WARNING

    if any(
        w in haystack
        for w in (
            "directionally",
            "move window position",
            "move / resize",
            "silent",
            "scroll",
            "cycle workspaces",
            "wi-fi",
            "bt",
            "audio",
            "vol+",
            "vol-",
            "bri+",
            "bri-",
        )
    ) or any(k in keys for k in ("H/J/K/L", "←/↓/↑/→", "DRAG", "SCROLL", "1 / 2 / 3")):
        return Tag.MUTED

    if any(
        w in haystack
        for w in (
            "launcher",
            "search",
            "menu",
            "emoji",
            "lens",
            "screenshot",
            "capture",
            "record",
            "last active",
            "toggle scratchpad",
            "cycle next",
        )
    ):
        return Tag.SUCCESS

    return Tag.ACCENT


def score_priority(cat_id: CategoryId, action: str, keys: KeyTuple, origin: str) -> int:
    """Lower number = higher priority inside its category card."""
    act = action.lower()
    cust_bonus = -5 if origin == "CUST" else 0

    match cat_id:
        case CategoryId.LAUNCHERS:
            order = (
                "terminal", "browser", "file manager", "editor", "launcher",
                "menu", "clipboard", "lens", "search", "emoji",
            )
        case CategoryId.WINDOWS:
            order = (
                "close", "fullscreen", "float", "split", "pin",
                "maximize", "focus direction", "move window position", "move / resize",
            )
        case CategoryId.WORKSPACES:
            order = (
                "switch workspace", "move window to ws", "silent move",
                "last active", "toggle scratchpad", "send to scratchpad",
                "special", "spotify", "cycle workspaces", "cycle next",
            )
        case CategoryId.SYSTEM:
            order = (
                "lock", "power", "logout", "activity", "quick screenshot",
                "screenshot", "color picker", "ollama", "ai ", "wi-fi",
                "audio", "reload", "keybind", "cheatsheet",
            )

    for idx, keyword in enumerate(order):
        if keyword in act:
            return (idx * 5) + cust_bonus
    return 60 + cust_bonus


# ── Range, Directional & Alternative Chord Compressor ───────────────────────
_WS_NUM_RE: Final[re.Pattern[str]] = re.compile(r"^(.*?\b)([1-9]|10)(\b.*)$")
_DIR_HJKL: Final[frozenset[str]] = frozenset({"H", "J", "K", "L"})
_DIR_ARROWS: Final[frozenset[str]] = frozenset({"LEFT", "DOWN", "UP", "RIGHT"})
_DIR_WORD_RE: Final[re.Pattern[str]] = re.compile(
    r"\b(left|right|up|down|west|east|north|south|l|r|u|d)\b", re.IGNORECASE
)


def compress_and_categorize(
    raw_binds: list[RawHyprBind],
    keycode_map: dict[str, str],
) -> tuple[list[Category], list[Bind]]:
    """
    Transform raw Hyprland + Lua AST binds into:
      1. Compressed, categorized `Category` cards for the 2x2 Rich dashboard.
      2. Full uncompressed `Bind` list for interactive `fzf` search & execution.
    """
    all_uncompressed: list[Bind] = []
    default_submap_binds: list[tuple[KeyTuple, str, RawHyprBind]] = []

    for rb in raw_binds:
        norm_key = normalize_key_token(rb.key, keycode_map)
        keys: KeyTuple = (*rb.mods, norm_key) if norm_key else rb.mods
        raw_action = rb.description or f"{rb.dispatcher} {rb.arg}".strip()
        if not raw_action or not keys:
            continue

        cat_id, clean_act = resolve_category_and_clean_action(
            raw_action, rb.dispatcher, rb.arg, keys, rb.cat_hint
        )
        tag = infer_tag(cat_id, clean_act, rb.dispatcher, rb.arg, keys, rb.tag_hint)
        prio = score_priority(cat_id, clean_act, keys, rb.origin)

        all_uncompressed.append(
            Bind(
                keys=keys,
                action=clean_act,
                tag=tag,
                dispatcher=rb.dispatcher,
                arg=rb.arg,
                submap=rb.submap,
                origin=rb.origin,
                priority=prio,
            )
        )
        if not rb.submap:
            default_submap_binds.append((keys, clean_act, rb))

    # Pass 1: Group numeric ranges (1..9), directional quartets (H/J/K/L), and mouse drags/scrolls
    num_buckets: dict[tuple[KeyTuple, str, str, str], list[tuple[int, RawHyprBind]]] = {}
    dir_buckets: dict[tuple[KeyTuple, str, str], list[tuple[str, RawHyprBind]]] = {}
    mouse_drags: dict[KeyTuple, list[RawHyprBind]] = {}
    mouse_scrolls: dict[KeyTuple, list[RawHyprBind]] = {}
    tui_trio: dict[KeyTuple, list[tuple[str, str, RawHyprBind]]] = {}
    singles: list[tuple[KeyTuple, str, RawHyprBind]] = []

    for keys, act, rb in default_submap_binds:
        mods, last_key = keys[:-1], keys[-1]
        dsp_low = rb.dispatcher.lower()

        # Mouse drag (movewindow / resizewindow)
        if last_key == "DRAG" or dsp_low in ("movewindow", "resizewindow") and rb.mouse:
            mouse_drags.setdefault(mods or ("SUPER",), []).append(rb)
            continue

        # Mouse scroll workspace cycle
        if last_key == "SCROLL" and "workspace" in dsp_low:
            mouse_scrolls.setdefault(mods or ("SUPER",), []).append(rb)
            continue

        # ALT + 1/2/3 TUI trio (Wi-Fi / BT / Audio)
        if mods == ("ALT",) and last_key in ("1", "2", "3") and any(
            w in act.lower() for w in ("wi-fi", "wifi", "bt", "bluetooth", "audio", "tui")
        ):
            tui_trio.setdefault(mods, []).append((last_key, act, rb))
            continue

        # Numeric sequences (1..9 or 0)
        if last_key.isdigit():
            num_val = 10 if last_key == "0" else int(last_key)
            if m := _WS_NUM_RE.match(act):
                sig = (mods, m.group(1).strip(), m.group(3).strip(), dsp_low)
                num_buckets.setdefault(sig, []).append((num_val, rb))
                continue
            if "workspace" in dsp_low:
                sig = (mods, act, "", dsp_low)
                num_buckets.setdefault(sig, []).append((num_val, rb))
                continue

        # Directional sets (H/J/K/L or Arrows)
        if last_key in _DIR_HJKL or last_key in _DIR_ARROWS:
            kind = "H/J/K/L" if last_key in _DIR_HJKL else "←/↓/↑/→"
            if dsp_low in ("movefocus", "movewindow", "swapwindow", "resizeactive") or _DIR_WORD_RE.search(act):
                base_act = _DIR_WORD_RE.sub("", act).strip(" -–—:()/").strip()
                if dsp_low == "movefocus" or "focus" in base_act.lower():
                    base_act = "Focus Directionally"
                elif dsp_low in ("movewindow", "swapwindow") or "move" in base_act.lower():
                    base_act = "Move Window Position"
                elif dsp_low == "resizeactive" or "resize" in base_act.lower():
                    base_act = "Resize Window"
                dir_buckets.setdefault((mods, base_act, kind), []).append((last_key, rb))
                continue

        singles.append((keys, act, rb))

    compressed_rows: list[tuple[KeyTuple, str, RawHyprBind]] = []

    for (mods, prefix, suffix, dsp_low), num_items in num_buckets.items():
        if len(num_items) >= 3:
            sample_rb = num_items[0][1]
            if dsp_low == "workspace":
                label = "Switch Workspace 1–9"
            elif dsp_low == "movetoworkspace":
                label = "Move Window to WS 1–9"
            elif dsp_low == "movetoworkspacesilent":
                label = "Silent Move Window to WS"
            else:
                label = f"{prefix} 1–9 {suffix}".strip()
            compressed_rows.append(((*mods, "1..9"), label, sample_rb))
        else:
            for num_val, rb in num_items:
                k_str = "0" if num_val == 10 else str(num_val)
                compressed_rows.append(((*mods, k_str), rb.description or prefix, rb))

    for (mods, base_act, kind), dir_items in dir_buckets.items():
        if len(dir_items) >= 3:
            compressed_rows.append(((*mods, kind), base_act, dir_items[0][1]))
        else:
            for k_str, rb in dir_items:
                compressed_rows.append(((*mods, k_str), rb.description or base_act, rb))

    for mods, rbs in mouse_drags.items():
        compressed_rows.append(((*mods, "DRAG"), "Move / Resize Window", rbs[0]))

    for mods, rbs in mouse_scrolls.items():
        compressed_rows.append(((*mods, "SCROLL"), "Cycle Workspaces", rbs[0]))

    for mods, trio_items in tui_trio.items():
        if len(trio_items) >= 2:
            compressed_rows.append(((*mods, "1 / 2 / 3"), "Wi-Fi / BT / Audio TUI", trio_items[0][2]))
        else:
            for k_str, act, rb in trio_items:
                compressed_rows.append(((*mods, k_str), act, rb))

    # Pass 2: Guarded Alternative Chord Pairing (merge identical action + dispatcher + arg if <= 21 chars)
    merged_rows: list[tuple[KeyTuple, str, RawHyprBind]] = []
    action_index: dict[tuple[str, str, str], int] = {}

    for keys, act, rb in [*compressed_rows, *singles]:
        sig = (act.lower(), rb.dispatcher, rb.arg)
        if rb.dispatcher and sig in action_index:
            idx = action_index[sig]
            prev_keys, prev_act, prev_rb = merged_rows[idx]
            if prev_keys != keys:
                if prev_keys[:-1] == keys[:-1]:
                    cand: KeyTuple = (*prev_keys[:-1], f"{prev_keys[-1]} / {keys[-1]}")
                else:
                    cand = (*prev_keys, "/", *keys)
                if len(" + ".join(cand)) <= MAX_CHORD_WIDTH:
                    merged_rows[idx] = (cand, prev_act, prev_rb)
                    continue
            else:
                continue

        action_index[sig] = len(merged_rows)
        merged_rows.append((keys, act, rb))

    # Pass 3: Route into the 4 Dusky Categories
    buckets: dict[CategoryId, list[Bind]] = {
        CategoryId.LAUNCHERS: [],
        CategoryId.WINDOWS: [],
        CategoryId.WORKSPACES: [],
        CategoryId.SYSTEM: [],
    }

    for keys, act, rb in merged_rows:
        cat_id, clean_act = resolve_category_and_clean_action(
            act, rb.dispatcher, rb.arg, keys, rb.cat_hint
        )
        tag = infer_tag(cat_id, clean_act, rb.dispatcher, rb.arg, keys, rb.tag_hint)
        prio = score_priority(cat_id, clean_act, keys, rb.origin)
        buckets[cat_id].append(
            Bind(
                keys=keys,
                action=clean_act,
                tag=tag,
                dispatcher=rb.dispatcher,
                arg=rb.arg,
                submap=rb.submap,
                origin=rb.origin,
                priority=prio,
            )
        )

    categories: list[Category] = []
    for cat_id in (
        CategoryId.LAUNCHERS,
        CategoryId.WINDOWS,
        CategoryId.WORKSPACES,
        CategoryId.SYSTEM,
    ):
        icon, accent = category_meta(cat_id)
        sorted_binds = tuple(sorted(buckets[cat_id], key=lambda b: (b.priority, b.action)))
        categories.append(
            Category(
                title=cat_id.value,
                icon=icon,
                accent=accent,
                binds=sorted_binds,
            )
        )

    return categories, all_uncompressed


# ── High-Performance Content-Addressed Cache (<2ms Warm Start) ──────────────
def file_fingerprint(path: Path) -> str:
    try:
        st = path.stat()
        return f"{st.st_mtime_ns}:{st.st_size}"
    except OSError:
        return "missing"


def compute_state_signature(raw_hypr_binds: str) -> str:
    h = hashlib.sha256()
    h.update(CACHE_SCHEMA_VERSION.encode("utf-8"))
    h.update(os.environ.get("HYPRLAND_INSTANCE_SIGNATURE", "no-his").encode("utf-8"))
    h.update(file_fingerprint(SOURCE_LUA).encode("utf-8"))
    h.update(file_fingerprint(CUSTOM_LUA).encode("utf-8"))
    h.update(file_fingerprint(MATUGEN_PATH).encode("utf-8"))
    if raw_hypr_binds:
        h.update(raw_hypr_binds.encode("utf-8", errors="replace"))
    return h.hexdigest()


def serialize_state(
    signature: str,
    categories: list[Category],
    all_binds: list[Bind],
) -> None:
    try:
        CACHE_DIR.mkdir(parents=True, exist_ok=True)
        payload = {
            "signature": signature,
            "categories": [
                {
                    "title": c.title,
                    "binds": [asdict(b) for b in c.binds],
                }
                for c in categories
            ],
            "all_binds": [asdict(b) for b in all_binds],
        }
        fd, tmp_name = tempfile.mkstemp(dir=CACHE_DIR, prefix=".kb_cache_")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump(payload, f, ensure_ascii=False)
            os.replace(tmp_name, CACHE_FILE)
        finally:
            if os.path.exists(tmp_name):
                os.unlink(tmp_name)
    except OSError:
        pass


def deserialize_state(
    expected_signature: str,
) -> tuple[tuple[Category, ...], list[Bind]] | None:
    if not CACHE_FILE.exists():
        return None
    try:
        data: dict[str, Any] = json.loads(CACHE_FILE.read_text(encoding="utf-8"))
        if data.get("signature") != expected_signature:
            return None

        categories: list[Category] = []
        for c_raw in data.get("categories", []):
            cat_id = CategoryId(c_raw["title"])
            icon, accent = category_meta(cat_id)
            binds = tuple(
                Bind(
                    keys=tuple(b["keys"]),
                    action=b["action"],
                    tag=Tag(b["tag"]),
                    dispatcher=b.get("dispatcher", ""),
                    arg=b.get("arg", ""),
                    submap=b.get("submap", ""),
                    origin=b.get("origin", "SRC"),
                    priority=int(b.get("priority", 50)),
                )
                for b in c_raw.get("binds", [])
            )
            categories.append(Category(title=cat_id.value, icon=icon, accent=accent, binds=binds))

        all_binds = [
            Bind(
                keys=tuple(b["keys"]),
                action=b["action"],
                tag=Tag(b["tag"]),
                dispatcher=b.get("dispatcher", ""),
                arg=b.get("arg", ""),
                submap=b.get("submap", ""),
                origin=b.get("origin", "SRC"),
                priority=int(b.get("priority", 50)),
            )
            for b in data.get("all_binds", [])
        ]
        if not categories or all(len(c.binds) == 0 for c in categories):
            return None
        return tuple(categories), all_binds
    except Exception:
        return None


def load_keybinds_state(
    *, force_refresh: bool = False
) -> tuple[tuple[Category, ...], list[Bind]]:
    """Load keybinds from cache or rebuild from live Hyprland socket + Lua AST."""
    raw_hypr = hypr_ipc("binds")
    sig = compute_state_signature(raw_hypr)

    if not force_refresh:
        cached = deserialize_state(sig)
        if cached is not None:
            return cached

    lua_src = parse_lua_binds(SOURCE_LUA, "SRC")
    lua_cust = parse_lua_binds(CUSTOM_LUA, "CUST")
    lua_all = [*lua_src, *lua_cust]

    if raw_hypr.strip():
        live_binds = parse_live_hypr_binds(raw_hypr, lua_all)
    else:
        live_binds = fallback_binds_from_ast(lua_all)

    if not live_binds:
        flat_fallback = [b for cat in FALLBACK_CATALOGUE for b in cat.binds]
        return FALLBACK_CATALOGUE, flat_fallback

    layout, variant = get_leading_keyboard()
    keycode_map = build_keycode_map(layout, variant)
    categories, all_uncompressed = compress_and_categorize(live_binds, keycode_map)

    if raw_hypr.strip():
        serialize_state(sig, categories, all_uncompressed)

    return tuple(categories), all_uncompressed


# ── Rich Dashboard Renderer ─────────────────────────────────────────────────
def _pill(token: str, cat_accent: str) -> Text:
    """Concise keycap token using Matugen colors."""
    bare = token.strip()
    out = Text()
    if bare in MODS:
        out.append(bare, style=f"bold {C.FG}")
    elif any(ch in bare for ch in ("..", "/", "←", "↓", "↑", "→")):
        out.append(bare, style=f"bold {C.WARNING}")
    else:
        out.append(bare, style=f"bold {cat_accent}")
    return out


def chord(keys: KeyTuple, cat_accent: str) -> Text:
    """Keycaps joined by a delicate + (or / for alternative chords)."""
    line = Text()
    for i, k in enumerate(keys):
        if i:
            if k == "/" or keys[i - 1] == "/":
                line.append(" ", style=f"bold {C.FG}")
            else:
                line.append(" + ", style=f"bold {C.FG}")
        line.append_text(_pill(k, cat_accent))
    return line


def action_cell(b: Bind) -> Text:
    """Status gem + high-contrast verb."""
    fg = tag_fg(b.tag)
    cell = Text()
    cell.append("● ", style=fg)
    cell.append(b.action, style=f"bold {fg}")
    return cell


def build_card(cat: Category, index: int, *, show_all: bool = False) -> Panel:
    """One category card — 2 clean columns, zero dots truncation."""
    tbl = Table(
        show_header=True,
        header_style=f"bold underline {C.FG}",
        show_edge=False,
        box=None,
        expand=True,
        pad_edge=False,
        padding=(0, 1),
    )
    tbl.add_column("COMBINATION", justify="left", no_wrap=True, width=MAX_CHORD_WIDTH)
    tbl.add_column("ACTION", justify="left", no_wrap=True)

    visible_binds = cat.binds if show_all else cat.binds[:DEFAULT_CARD_LIMIT]
    for b in visible_binds:
        tbl.add_row(chord(b.keys, cat.accent), action_cell(b))

    title = Text()
    title.append(f" {cat.icon} ", style=f"bold {cat.accent}")
    title.append(f" {index:02d} ", style=f"bold {C.BG} on {cat.accent}")
    title.append(f"  {cat.title} ", style=f"bold {cat.accent}")

    hidden_count = max(0, len(cat.binds) - len(visible_binds))
    subtitle = (
        Text(f" +{hidden_count} more [a/f] ", style=f"dim {C.FG}")
        if hidden_count > 0
        else None
    )

    return Panel(
        tbl,
        title=title,
        title_align="left",
        subtitle=subtitle,
        subtitle_align="right",
        border_style=cat.accent,
        box=box.ROUNDED,
        padding=(0, 1),
        expand=True,
    )


def render(
    console: Console,
    catalogue: tuple[Category, ...],
    *,
    show_all: bool = False,
) -> Group:
    """Pure minimal dashboard — centered header title + 2x2 category grid + hint bar."""
    cards = [build_card(cat, i, show_all=show_all) for i, cat in enumerate(catalogue, 1)]

    if console.width >= 110 and len(cards) >= 4:
        grid = Table.grid(expand=True, padding=(0, 2))
        grid.add_column(ratio=1)
        grid.add_column(ratio=1)
        grid.add_row(cards[0], cards[1])
        grid.add_row(Text(""), Text(""))
        grid.add_row(cards[2], cards[3])
    else:
        grid = Table.grid(expand=True, padding=(0, 0))
        grid.add_column(ratio=1)
        for i, card in enumerate(cards):
            grid.add_row(card)
            if i < len(cards) - 1:
                grid.add_row(Text(""))

    brand = Text()
    brand.append(" 󰌌  DUSKY CHEATSHEET ", style=f"bold {C.BG} on {C.ACCENT}")

    footer = Text()
    footer.append(" [/] ", style=f"bold {C.ACCENT}")
    footer.append("Search & Run   ", style=f"dim {C.FG}")
    footer.append("[e] ", style=f"bold {C.SUCCESS}")
    footer.append("Edit Binds   ", style=f"dim {C.FG}")
    footer.append("[a] ", style=f"bold {C.WARNING}")
    footer.append("Toggle All   ", style=f"dim {C.FG}")
    footer.append("[r] ", style=f"bold {C.ACCENT}")
    footer.append("Reload   ", style=f"dim {C.FG}")
    footer.append("[q / Esc] ", style=f"bold {C.ERROR}")
    footer.append("Close", style=f"dim {C.FG}")

    return Group(
        Text(""),
        Align.center(brand),
        Text(""),
        grid,
        Text(""),
        Align.center(footer),
    )


# ── Interactive Dispatch & Editor Integration ───────────────────────────────
def dispatch_bind(b: Bind) -> None:
    """Execute a selected keybinding via Hyprland UNIX socket or hyprctl."""
    dsp = b.dispatcher.strip()
    arg = b.arg.strip()
    if not dsp:
        return

    if dsp == "exec":
        if arg:
            hypr_ipc(f"dispatch exec {arg}")
    elif dsp == "lua":
        if arg:
            hypr_ipc(f"dispatch {arg}")
    elif arg:
        hypr_ipc(f"dispatch {dsp} {arg}")
    else:
        hypr_ipc(f"dispatch {dsp}")


def launch_fuzzy_search(all_binds: list[Bind]) -> bool:
    """
    Open fzf styled with Matugen palette to search and dispatch any keybind.
    Returns True if a bind was selected and dispatched, or False on Esc/Ctrl+C.
    """
    if not shutil.which("fzf") or not all_binds:
        return False

    rows: list[str] = []
    for idx, b in enumerate(all_binds):
        sub_prefix = f"[{b.submap}] " if b.submap else ""
        chord_str = " + ".join(b.keys)
        origin_tag = "★ " if b.origin == "CUST" else "  "
        rows.append(f"{origin_tag}{chord_str:<28} → {sub_prefix}{b.action}\t{idx}")

    env = os.environ.copy()
    env.pop("FZF_DEFAULT_OPTS", None)
    env.pop("FZF_DEFAULT_OPTS_FILE", None)

    color_spec = (
        f"bg+:{C.BG},fg:{C.FG},fg+:{C.ACCENT},hl:{C.SUCCESS},"
        f"hl+:{C.SUCCESS},prompt:{C.ACCENT},pointer:{C.ERROR},"
        f"border:{C.ACCENT},header:{C.WARNING},info:{C.MUTED}"
    )

    cmd = [
        "fzf",
        "--delimiter=\t",
        "--with-nth=1",
        "--layout=reverse",
        "--border=rounded",
        "--info=inline",
        "--prompt=󰌌 Run Keybind > ",
        "--header=Enter: Execute Keybind  │  Esc: Return to Cheatsheet  │  ★ = Custom Override",
        f"--color={color_spec}",
    ]

    try:
        res = subprocess.run(
            cmd,
            input="\n".join(rows),
            stdout=subprocess.PIPE,
            text=True,
            env=env,
        )
    except OSError:
        return False

    if res.returncode == 0 and res.stdout.strip():
        try:
            chosen_idx = int(res.stdout.rstrip("\n").rsplit("\t", 1)[1])
            if 0 <= chosen_idx < len(all_binds):
                dispatch_bind(all_binds[chosen_idx])
                return True
        except (ValueError, IndexError):
            pass
    return False


def launch_keybind_editor() -> bool:
    """Run `dusky_keybinds.py` as a foreground subprocess and return when done."""
    editor_script = Path(__file__).resolve().with_name("dusky_keybinds.py")
    if not editor_script.exists():
        return False
    try:
        subprocess.run([sys.executable, str(editor_script)], check=False)
        return True
    except (OSError, KeyboardInterrupt):
        return False


def read_single_keypress() -> str:
    """
    Unbuffered raw single-keystroke reader using os.read(fd, 1).
    Cleanly distinguishes a bare Esc keystroke ('\\x1b') from multi-byte CSI/SS3
    navigation sequences ('ESC_SEQ') and handles Ctrl+C ('\\x03') / Ctrl+D ('\\x04').
    """
    if not sys.stdin.isatty():
        return "q"
    fd = sys.stdin.fileno()
    prev = termios.tcgetattr(fd)
    try:
        tty.setraw(fd)
        raw = os.read(fd, 1)
        if not raw:
            return "\x04"
        ch = raw.decode("utf-8", errors="replace")
        if ch == "\x1b":
            # Wait briefly to see if trailing escape sequence bytes follow
            if select.select([fd], [], [], 0.025)[0]:
                while select.select([fd], [], [], 0.010)[0]:
                    if not os.read(fd, 64):
                        break
                return "ESC_SEQ"
            return "\x1b"
        return ch
    except (KeyboardInterrupt, EOFError, OSError):
        return "\x03"
    finally:
        try:
            termios.tcflush(fd, termios.TCIFLUSH)
        except termios.error:
            pass
        termios.tcsetattr(fd, termios.TCSADRAIN, prev)


def main() -> None:
    parser = argparse.ArgumentParser(description="Dusky Dynamic Keybinds Cheatsheet")
    parser.add_argument(
        "--refresh", "-r", action="store_true", help="Force cache invalidation and rebuild"
    )
    parser.add_argument(
        "--all", "-a", action="store_true", help="Display all binds without the 9-row card cap"
    )
    parser.add_argument(
        "--print", "-p", action="store_true", help="Print formatted keybindings to stdout and exit"
    )
    args = parser.parse_args()

    console = Console(highlight=False)
    catalogue, all_binds = load_keybinds_state(force_refresh=args.refresh)
    show_all = args.all

    if args.print:
        console.print(render(console, catalogue, show_all=show_all))
        return

    while True:
        console.clear()
        console.print(render(console, catalogue, show_all=show_all))
        key = read_single_keypress()

        match key:
            case "q" | "Q" | "\x1b" | "\x03" | "\x04":
                return
            case "/" | "f" | "F":
                console.clear()
                if launch_fuzzy_search(all_binds):
                    return
                continue
            case "e" | "E":
                console.clear()
                if launch_keybind_editor():
                    Palette.load()
                    catalogue, all_binds = load_keybinds_state(force_refresh=True)
                continue
            case "a" | "A" | "\t":
                show_all = not show_all
                continue
            case "r" | "R":
                Palette.load()
                catalogue, all_binds = load_keybinds_state(force_refresh=True)
                continue
            case _:
                continue


if __name__ == "__main__":
    main()
