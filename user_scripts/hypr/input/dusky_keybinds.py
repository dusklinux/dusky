#!/usr/bin/env python3
"""Inspect and edit Hyprland Lua bind declarations without moving their scope.

The list is a static view of the two keybind files, not an evaluator of Lua.
Conditions, loops and callbacks can register a different set of live bindings.
Edits (including SRC edits) stay in the original file; duplicate chords are legal.
"""

import argparse
lazy import fcntl
from bisect import bisect_right
import os
import re
import shlex
import shutil
lazy import signal
import subprocess
import sys
lazy import tempfile
from dataclasses import dataclass, field
from pathlib import Path

try:
    from rich.console import Console, Group
    from rich.panel import Panel
    from rich.syntax import Syntax
    from rich.text import Text
except ImportError:
    sys.exit("Missing python-rich. Install it with the ISO packages or pacman -S python-rich.")

console = Console(highlight=False)
_config = Path(os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config")
if not _config.is_absolute():
    _config = Path.home() / ".config"
SOURCE_LUA = _config / "hypr/source/keybinds.lua"
CUSTOM_LUA = _config / "hypr/edit_here/source/keybinds.lua"
EMPTY_TEMPLATE = 'hl.bind("SUPER + F12", hl.dsp.exec_cmd(""), { description = "" })'


@dataclass(frozen=True, slots=True)
class Token:
    value: str
    start: int
    end: int
    string: bool = False


@dataclass(frozen=True, slots=True)
class Bind:
    key_str: str
    dispatcher: str
    options: str
    description: str
    submap: str
    raw_call: str
    origin: str
    char_start: int
    char_end: int
    file_text: str = field(repr=False, compare=False)
    is_unbind: bool = False
    dynamic: bool = False
    line: int = 0


_LUA_SPACE = " \f\n\r\t\v"
_STRING_ESCAPES = frozendict({"a": "\a", "b": "\b", "f": "\f", "n": "\n", "r": "\r", "t": "\t", "v": "\v"})
_NEWLINE = re.compile(r"\r\n|\n\r|\r|\n")
_LONG_OPEN = re.compile(r"\[(=*)\[")
# Consume whitespace and quoted strings in the regex engine rather than one
# Python iteration per character. Possessive repetitions keep unfinished
# strings linear, including escaped whitespace. Long delimiters need a search.
_LEX = re.compile(
    r'(?P<space>\s+)|(?P<comment>--)|(?P<long>\[(=*)\[)|'
    r'''(?P<quoted>"(?:\\(?:z\s*+|\r\n|\n\r|[\s\S])|[^"\\\r\n]++)*+"|'(?:\\(?:z\s*+|\r\n|\n\r|[\s\S])|[^'\\\r\n]++)*+')|'''
    r'[A-Za-z_][A-Za-z_0-9]*|\.\.\.|\.\.|==|~=|<=|>=|::|[^\s]', re.ASCII
)


def lua_tokens(code: str) -> list[Token]:
    """Tokenize strings/comments atomically; never execute configuration code."""
    result = []
    i, n = 0, len(code)
    while i < n:
        match = _LEX.prefixmatch(code, i)
        assert match is not None
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
                    raise ValueError(f"Unclosed Lua long string/comment at offset {start}")
                i = end + len(close)
            else:
                end = _NEWLINE.search(code, i)
                i = n if end is None else end.start()
            if kind == "comment":
                continue
        elif code[start] in "\"'" and kind != "quoted":
            raise ValueError(f"Unclosed Lua string at offset {start}")
        result.append(Token(code[start:i], start, i, kind in ("quoted", "long")))
    return result


def lua_string_bytes(value: str) -> bytes:
    match = _LONG_OPEN.prefixmatch(value)
    if match:
        body = value[match.end():-(len(match[1]) + 2)]
        body = _NEWLINE.sub("\n", body)
        return (body[1:] if body.startswith("\n") else body).encode("utf-8")
    body = value[1:-1]
    if "\\" not in body:
        return body.encode("utf-8")
    # Lua hexadecimal/decimal escapes represent bytes, not Unicode characters.
    out = bytearray()
    i = 0
    while i < len(body):
        if body[i] != "\\":
            out.extend(body[i].encode("utf-8"))
            i += 1
            continue
        i += 1
        char = body[i]
        if char == "z":
            i += 1
            while i < len(body) and body[i] in _LUA_SPACE:
                i += 1
        elif char == "x":
            out.append(int(body[i + 1:i + 3], 16))
            i += 3
        elif char == "u" and body[i + 1:i + 2] == "{":
            end = body.index("}", i + 2)
            point = int(body[i + 2:end], 16)
            if not 0 <= point <= 0x7FFFFFFF:
                raise ValueError("Lua Unicode escape is outside its 31-bit range")
            if point <= 0x10FFFF and not 0xD800 <= point <= 0xDFFF:
                out.extend(chr(point).encode("utf-8"))
            else:
                # Lua permits extended UTF-8 and surrogate values too. Keep
                # those bytes intact until the final display decode.
                width = max(2, (point.bit_length() + 3) // 5)
                out.append((0xFF << (8 - width) & 0xFF) | (point >> (6 * (width - 1))))
                for shift in range(6 * (width - 2), -1, -6):
                    out.append(0x80 | (point >> shift & 0x3F))
            i = end + 1
        elif char.isascii() and char.isdigit():
            match = re.prefixmatch(r"[0-9]{1,3}", body[i:])
            out.append(int(match[0]))
            i += len(match[0])
        elif char in "\r\n":
            out.append(10)
            i += 2 if body[i:i + 2] in ("\r\n", "\n\r") else 1
        else:
            out.extend(_STRING_ESCAPES.get(char, char).encode("utf-8"))
            i += 1
    return out.take_bytes()


def literal_string(tokens: list[Token]) -> str | None:
    # Resolve only literal concatenation. Lua variables have lexical scope and
    # may be reassigned; guessing their values can make an edit target the wrong key.
    if not tokens or len(tokens) % 2 == 0:
        return None
    if any(not t.string for t in tokens[::2]) or any(t.value != ".." for t in tokens[1::2]):
        return None
    return b"".join(lua_string_bytes(t.value) for t in tokens[::2]).decode("utf-8", errors="replace")


def call_arguments(tokens: list[Token], open_idx: int, separators: tuple[str, ...] = (",",)) -> tuple[list[list[Token]], int]:
    """Find argument spans, including commas inside Lua function bodies."""
    brackets = [")"]
    blocks = []
    args = []
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
            if not blocks:
                raise ValueError("Unbalanced Lua block in bind call")
            blocks.pop()
        elif value in ("(", "[", "{"):
            brackets.append({"(": ")", "[": "]", "{": "}"}[value])
        elif value in (")", "]", "}"):
            if not brackets or value != brackets.pop():
                raise ValueError("Unbalanced Lua delimiters in bind call")
            if not brackets:
                if blocks:
                    raise ValueError("Unclosed Lua function/block in bind call")
                if start < i:
                    args.append(tokens[start:i])
                return args, i
        elif value in separators and len(brackets) == 1 and not blocks:
            args.append(tokens[start:i])
            start = i + 1
    raise ValueError("Unclosed Lua bind call")


def token_text(text: str, tokens: list[Token]) -> str:
    return text[tokens[0].start:tokens[-1].end] if tokens else ""


def parse_lua_file_content(text: str, origin: str) -> list[Bind]:
    tokens = lua_tokens(text)
    regions = []
    calls = []
    newlines = [match.start() for match in _NEWLINE.finditer(text)]
    # Suppress the implementation body of the known local wrapper, while
    # retaining dynamic hl.bind(key, ...) declarations elsewhere.
    stack = []
    wrapper_regions = []
    for i, token in enumerate(tokens):
        if token.string:
            continue
        value = token.value
        if value in ("function", "if", "for", "while", "repeat"):
            wrapper = value == "function" and i + 1 < len(tokens) and tokens[i + 1].value == "cond_bind"
            stack.append((value, token.start, wrapper))
        elif value == "do":
            if stack and stack[-1][0] in ("for", "while"):
                _, start, wrapper = stack.pop()
                stack.append(("loop", start, wrapper))
            else:
                stack.append(("do", token.start, False))
        elif value in ("end", "until") and stack:
            _, start, wrapper = stack.pop()
            if wrapper:
                wrapper_regions.append((start, token.end))
    for i, token in enumerate(tokens):
        if token.string or i + 1 >= len(tokens):
            continue
        if any(start < token.start < end for start, end in wrapper_regions):
            continue
        # cond_bind is the local wrapper used by the distributed source file.
        if token.value == "cond_bind" and tokens[i + 1].value == "(":
            if i and tokens[i - 1].value in ("function", ".", ":"):
                continue
            name, op = "bind", i + 1
        elif (token.value == "hl" and i + 3 < len(tokens)
              and tokens[i + 1].value == "." and tokens[i + 3].value == "("
              and tokens[i + 2].value in ("bind", "unbind", "define_submap")
              and (not i or tokens[i - 1].value not in (".", ":"))):
            name, op = tokens[i + 2].value, i + 3
        else:
            continue
        args, end_idx = call_arguments(tokens, op)
        if name == "define_submap":
            if not args or not args[0]:
                raise ValueError("Submap declaration is missing a name")
            name_value = literal_string(args[0])
            regions.append((token.start, tokens[end_idx].end,
                            name_value if name_value is not None else "<" + token_text(text, args[0]) + ">"))
        else:
            calls.append((token, name, args, end_idx))
    binds = []
    for token, name, args, end_idx in calls:
        if not args or (name == "bind" and len(args) < 2):
            continue
        key = literal_string(args[0])
        key_text = key if key is not None else "<" + token_text(text, args[0]) + ">"
        options = token_text(text, args[2]) if len(args) > 2 else ""
        desc = ""
        if len(args) > 2 and args[2] and args[2][0].value == "{" and args[2][-1].value == "}":
            # Reuse the delimiter/block splitter for table fields. Only a
            # literal description at this table's top level is knowable here.
            fields, _ = call_arguments([Token("(", 0, 0), *args[2][1:-1], Token(")", 0, 0)], 0, (",", ";"))
            for field in fields:
                if len(field) >= 3 and field[0].value == "description" and field[1].value == "=":
                    desc = literal_string(field[2:]) or ""
        enclosing = [(s, e, sub) for s, e, sub in regions if s < token.start < e]
        submap = min(enclosing, key=lambda r: r[1] - r[0])[2] if enclosing else ""
        end = tokens[end_idx].end
        binds.append(Bind(key_text,
                          token_text(text, args[1]) if len(args) > 1 else "UNBIND",
                          options, desc, submap, text[token.start:end], origin,
                          token.start, end, text, name == "unbind", key is None,
                          bisect_right(newlines, token.start) + 1))
    return binds


def read_config(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8", newline="")
    except FileNotFoundError:
        return ""


def validate_lua(content: str) -> None:
    """Compile only: do not run dispatcher calls or configuration side effects."""
    result = subprocess.run(["luac", "-p", "-"], input=content,
                            capture_output=True, text=True, encoding="utf-8", timeout=10)
    if result.returncode:
        raise ValueError(result.stderr.strip() or "Lua compilation failed")


def atomic_write(content: str, path: Path, expected: str) -> None:
    validate_lua(content)
    real = path.resolve()
    real.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=real.parent, prefix=".keybinds_write_")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as file:
            file.write(content)
            file.flush()
            os.fsync(file.fileno())
        # Linux directory locks serialize saves from other manager instances
        # without a persistent lock file. External edits are checked again here.
        directory_fd = os.open(real.parent, os.O_RDONLY | os.O_DIRECTORY)
        try:
            fcntl.flock(directory_fd, fcntl.LOCK_EX)
            if read_config(path) != expected:
                raise ValueError("File changed while editing. Reopen it to merge your changes.")
            if real.exists():
                os.chmod(tmp, real.stat().st_mode & 0o7777)
            os.replace(tmp, real)
            try:
                os.fsync(directory_fd)
            except OSError as error:
                raise OSError(f"Configuration was replaced, but directory synchronization failed: {error}") from error
        finally:
            os.close(directory_fd)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)


def replace_call(text: str, bind: Bind, replacement: str) -> str:
    if text != bind.file_text:
        raise ValueError("File changed since selection; reopen the list before editing.")
    # Replace only the selected call. Never remove siblings, line prefixes,
    # submap wrappers, local declarations or a neighbouring unbind directive.
    return text[:bind.char_start] + replacement + text[bind.char_end:]


def reload_hyprland() -> None:
    if not os.environ.get("HYPRLAND_INSTANCE_SIGNATURE"):
        console.print("Saved; outside a Hyprland session, reload skipped.")
        return
    try:
        result = subprocess.run(["hyprctl", "reload", "config-only"], capture_output=True,
                                text=True, timeout=15)
        errors = subprocess.run(["hyprctl", "configerrors"], capture_output=True,
                                text=True, timeout=10)
        if result.returncode or errors.returncode or errors.stdout.strip():
            console.print(Text("Saved, but Hyprland reported a reload/configuration issue:\n" +
                               result.stdout + result.stderr + errors.stdout + errors.stderr, style="yellow"))
        else:
            console.print("[green]Saved and reloaded; Hyprland reports no configuration errors.[/green]")
    except (OSError, subprocess.TimeoutExpired) as error:
        console.print(Text(f"Saved; reload could not be verified: {error}", style="yellow"))


def get_editor_choice() -> str | None:
    candidates = [os.environ.get("VISUAL"), os.environ.get("EDITOR"), "nvim", "nano", "vi"]
    editors = []
    for candidate in candidates:
        if not candidate or candidate in editors:
            continue
        try:
            cmd = shlex.split(candidate)
        except ValueError:
            continue
        if cmd and shutil.which(cmd[0]):
            editors.append(candidate)
    console.print("\n[bold]Editor[/bold]")
    for i, editor in enumerate(editors, 1):
        console.print(Text(f"  {i}. {editor}"))
    console.print("  i. Inline input    b. Back")
    while True:
        choice = console.input("Editor [1/i/b] > ", markup=False).strip().lower()
        if choice == "b":
            return None
        if choice == "i":
            return "inline"
        if not choice and editors:
            return editors[0]
        if choice.isdigit() and 1 <= int(choice) <= len(editors):
            return editors[int(choice) - 1]


def edit_in_editor(initial: str, editor: str) -> str | None:
    if editor == "inline":
        console.print("Paste Lua; Ctrl+D on an empty line finishes, Ctrl+C cancels.")
        console.print(Syntax(initial, "lua", word_wrap=True))
        try:
            return "".join(sys.stdin)
        except KeyboardInterrupt:
            return None
    fd, name = tempfile.mkstemp(suffix=".lua", prefix="dusky_bind_")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as file:
            file.write(initial)
        command = [*shlex.split(editor), name]
        # The editor shares our terminal's foreground process group. Handle
        # SIGINT here so Ctrl+C remains the editor's command; exec resets the
        # child's caught signal handlers, and the original handler is restored.
        previous_sigint = signal.signal(signal.SIGINT, lambda *_: None)
        try:
            result = subprocess.run(command)
        finally:
            signal.signal(signal.SIGINT, previous_sigint)
        if result.returncode:
            console.print(Text(f"Editor exited with status {result.returncode}.", style="yellow"))
            return None
        return Path(name).read_text(encoding="utf-8", newline="")
    except (OSError, ValueError) as error:
        console.print(Text(f"Editor failed: {error}", style="red"))
        return None
    finally:
        Path(name).unlink(missing_ok=True)


def print_binding_info_box(bind: Bind) -> None:
    label = f"{bind.origin} line {bind.line} | {bind.key_str}"
    if bind.submap:
        label += f" | submap {bind.submap}"
    console.print(Panel(Group(Text(label, style="bold cyan"), Text(bind.description),
                              Syntax(bind.raw_call, "lua", word_wrap=True)), title="Binding declaration"))
    if bind.dynamic:
        console.print("[yellow]Dynamic key expression: may register multiple keys or run conditionally.[/yellow]")


def one_line(text: str) -> str:
    return "".join(c if c.isprintable() else " " for c in text)


def generate_bind_rows(source: list[Bind], custom: list[Bind]) -> tuple[list[str], list[Bind]]:
    # An additional bind is not an override. Keep duplicates and unbinds visible
    # in file order, including declarations in conditional branches.
    displayed = [*source, *custom]
    rows = []
    for i, bind in enumerate(displayed):
        kind = f"{bind.origin}/UNB" if bind.is_unbind else bind.origin
        sub = f" [{one_line(bind.submap)}]" if bind.submap else ""
        detail = bind.description or bind.dispatcher
        row = f"[{kind}] {bind.line:4}{sub}  {one_line(bind.key_str):32} | {one_line(detail)}"
        rows.append(f"{row}\t{i}")
    return rows, displayed


def edit_flow(bind: Bind | None = None, *, whole_path: Path | None = None) -> bool:
    path = whole_path or (SOURCE_LUA if bind and bind.origin == "SRC" else CUSTOM_LUA)
    snapshot = read_config(path)
    if bind and snapshot != bind.file_text:
        raise ValueError("File changed since selection; reopen the list before editing.")
    original = snapshot if whole_path else bind.raw_call if bind else EMPTY_TEMPLATE
    draft = original
    console.print(Text(f"Editing {path}", style="bold cyan"))
    if bind and bind.origin == "SRC":
        console.print("[yellow]This edits the source file in place, preserving local functions and submaps.[/yellow]")
    editor = get_editor_choice()
    if editor is None:
        return False
    while True:
        edited = edit_in_editor(draft, editor)
        if edited is None:
            return False
        if not whole_path:
            edited = edited.strip()
        if edited == original:
            return False
        try:
            if not whole_path:
                entries = parse_lua_file_content(edited, "TMP")
                roots = [entry for entry in entries if entry.char_start == 0 and entry.char_end == len(edited)]
                if len(roots) != 1:
                    raise ValueError("Enter one complete bind/unbind call. Use the whole-file editor for other Lua code.")
                entry = roots[0]
                if not entry.dynamic and not entry.is_unbind:
                    parts = [p.strip() for p in entry.key_str.split("+")]
                    if not all(parts) or len({p.casefold() for p in parts}) != len(parts):
                        raise ValueError("Empty/repeated key in key string; use the keysym 'plus' for a plus key.")
            if whole_path:
                content = edited
            elif bind:
                content = replace_call(snapshot, bind, edited)
            else:
                content = snapshot + ("\n" if snapshot and not snapshot.endswith("\n") else "") + edited + "\n"
            validate_lua(content)
        except (ValueError, OSError, subprocess.TimeoutExpired) as error:
            console.print(Text(str(error), style="red"))
            if console.input("[e] Edit again / [b] Back > ", markup=False).strip().lower() != "e":
                return False
            draft = edited
            continue
        console.print(Panel(Syntax(edited, "lua", word_wrap=True), title=Text(f"Save to {path}")))
        choice = console.input("[y] Save / [e] Edit again / [b] Back > ", markup=False).strip().lower()
        if choice == "e":
            draft = edited
            continue
        if choice != "y":
            return False
        try:
            atomic_write(content, path, snapshot)
        except (OSError, ValueError, subprocess.TimeoutExpired):
            # The editor's temporary file has already been closed and removed.
            # Preserve the complete candidate when a conflict or write fails.
            try:
                with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", suffix=".lua",
                                                 prefix="dusky-keybinds-recovery-", delete=False) as recovery:
                    recovery.write(content)
                console.print(Text(f"Save candidate preserved at {recovery.name}", style="yellow"))
            except OSError as error:
                console.print(Text(f"Could not preserve the draft: {error}. Copy the candidate below.", style="red"))
                console.print(Syntax(content, "lua", word_wrap=True))
            raise
        reload_hyprland()
        return True


def delete_flow(bind: Bind) -> bool:
    path = SOURCE_LUA if bind.origin == "SRC" else CUSTOM_LUA
    snapshot = read_config(path)
    print_binding_info_box(bind)
    console.print(Text(f"Remove only this call from {path}. Other calls on the same chord remain.", style="yellow"))
    content = replace_call(snapshot, bind, "")
    try:
        validate_lua(content)
    except ValueError:
        console.print("[yellow]This call is used inside another expression. Use the whole-file editor to remove it.[/yellow]")
        return False
    if console.input("Delete [y/N] > ", markup=False).strip().lower() != "y":
        return False
    atomic_write(content, path, snapshot)
    reload_hyprland()
    return True


def choose(rows: list[str], view: bool) -> int | None:
    header = ("Dusky Binds" + (" | VIEW ONLY" if view else "") +
              "\nLua declarations in file order; loops/conditions are not evaluated."
              "\nType to search | Enter: select | Esc: quit | SRC: source | CUST: custom")
    env = os.environ.copy()
    # User fzf defaults can change output framing, selection count or input mode.
    env.pop("FZF_DEFAULT_OPTS", None)
    env.pop("FZF_DEFAULT_OPTS_FILE", None)
    result = subprocess.run(["fzf", "--delimiter=\t", "--with-nth=1", "--layout=reverse",
                             "--border", "--info=inline", "--prompt=Search > ", f"--header={header}"],
                            input="\n".join(rows), stdout=subprocess.PIPE, text=True, env=env)
    if result.returncode in (1, 130):
        return None
    if result.returncode:
        raise ValueError(f"fzf failed (status {result.returncode}); see terminal error above.")
    try:
        return int(result.stdout.rstrip("\n").rsplit("\t", 1)[1])
    except (ValueError, IndexError) as error:
        raise ValueError("fzf returned an invalid selection") from error


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--view", action="store_true", help="Read-only declaration browser (never creates files)")
    args = parser.parse_args()
    for command in (("fzf",) if args.view else ("fzf", "luac")):
        if not shutil.which(command):
            parser.error(f"Required command not installed: {command}")
    if not sys.stdin.isatty() or not sys.stdout.isatty():
        parser.error("Run this TUI in a terminal")
    while True:
        try:
            parsed = []
            parse_errors = set()
            for path, origin in ((SOURCE_LUA, "SRC"), (CUSTOM_LUA, "CUST")):
                try:
                    parsed.append(parse_lua_file_content(read_config(path), origin))
                except ValueError as error:
                    console.print(Text(f"{path}: {error}. Use the whole-file editor to repair it.", style="red"))
                    parsed.append([])
                    parse_errors.add(origin)
            rows, displayed = generate_bind_rows(*parsed)
            rows = [
                ("[FILE] View whole custom keybind file" if args.view else "[FILE] Edit whole custom keybind file") + "\t-2",
                ("[FILE] View whole source keybind file" if args.view else "[FILE] Edit whole source keybind file") + "\t-3",
                *rows,
            ]
            if "CUST" in parse_errors:
                rows[0] = "[PARSE ERROR] " + rows[0]
            if "SRC" in parse_errors:
                rows[1] = "[PARSE ERROR] " + rows[1]
            if not args.view:
                rows.insert(0, "[+] Add custom bind (additional action, existing binds remain)\t-1")
            index = choose(rows, args.view)
            if index is None:
                return
            console.clear()
            if index in (-2, -3) and args.view:
                path = CUSTOM_LUA if index == -2 else SOURCE_LUA
                console.print(Panel(Syntax(read_config(path), "lua", word_wrap=True), title=Text(str(path))))
            elif index in (-1, -2, -3) and not args.view:
                edit_flow(whole_path={-2: CUSTOM_LUA, -3: SOURCE_LUA}.get(index))
            elif 0 <= index < len(displayed):
                bind = displayed[index]
                print_binding_info_box(bind)
                if args.view:
                    console.input("Press Enter to return... ", markup=False)
                    continue
                action = console.input("[e] Edit in place / [d] Delete call / [b] Back / [q] Quit > ", markup=False).strip().lower()
                if action == "q":
                    return
                if action == "e":
                    edit_flow(bind)
                elif action == "d":
                    delete_flow(bind)
                else:
                    continue
            console.input("Press Enter to return... ", markup=False)
        except (OSError, ValueError, subprocess.TimeoutExpired) as error:
            console.print(Text(f"Operation failed: {error}", style="red"))
            console.input("Press Enter to return... ", markup=False)


if __name__ == "__main__":
    try:
        main()
    except (KeyboardInterrupt, EOFError):
        pass
