"""Source spans, AST helpers, layout-aware editing, and validation for Hyprland Lua configs."""

from collections.abc import Iterator
from dataclasses import dataclass
import re
lazy import shutil
lazy import subprocess


class LuaSyntaxError(ValueError):
    """Raised when Lua lexical or syntactic validation fails."""


LUA_KEYWORDS: frozenset[str] = frozenset({
    "and", "break", "do", "else", "elseif", "end", "false", "for", "function",
    "goto", "if", "in", "local", "nil", "not", "or", "repeat", "return",
    "then", "true", "until", "while",
})

_RE_LONG_OPEN = re.compile(r"\[(=*)\[")
_RE_IDENT = re.compile(r"[A-Za-z_][A-Za-z_0-9]*")
_RE_WS = re.compile(r"\s+")

_MULTI_SYMBOLS: tuple[str, ...] = (
    "...", "==", "~=", "<=", ">=", "//", "<<", ">>", "..", "::",
)


@dataclass(slots=True)
class TableEntry:
    """Top-level entry inside a Lua table constructor with exact source offsets."""

    key: str | None
    key_raw: str
    entry_start: int
    entry_end: int
    key_start: int
    key_end: int
    value_start: int
    value_end: int
    sep_pos: int | None
    sep_char: str | None


def _scan_lua(
    text: str,
    *,
    strict: bool = False,
    include_comments: bool = False,
) -> list[tuple[str, str, int, int]]:
    """Single-pass pure-Python Lua tokenizer preserving exact character offsets.

    Emits backward-compatible 4-tuples `(kind, value, start, end)` with token
    kinds `'name'`, `'string'`, `'symbol'` (and `'comment'` when requested).
    """
    out: list[tuple[str, str, int, int]] = []
    i = 0
    n = len(text)

    while i < n:
        ch = text[i]

        # Whitespace
        if ch in " \t\r\n\f\v":
            m_ws = _RE_WS.match(text, i)
            i = m_ws.end() if m_ws else i + 1
            continue

        # Comments: --... or --[=[...]=]
        if ch == "-" and i + 1 < n and text[i + 1] == "-":
            start = i
            m_long = _RE_LONG_OPEN.match(text, i + 2)
            if m_long:
                close_delim = f"]{m_long.group(1)}]"
                close_pos = text.find(close_delim, m_long.end())
                if close_pos < 0:
                    if strict:
                        raise LuaSyntaxError(f"Unterminated long block comment at offset {start}")
                    end = n
                else:
                    end = close_pos + len(close_delim)
            else:
                nl = text.find("\n", i + 2)
                end = n if nl < 0 else nl
            if include_comments:
                out.append(("comment", text[start:end], start, end))
            i = end
            continue

        # Long-bracket strings: [=[...]=]
        if ch == "[":
            m_long = _RE_LONG_OPEN.match(text, i)
            if m_long:
                start = i
                close_delim = f"]{m_long.group(1)}]"
                close_pos = text.find(close_delim, m_long.end())
                if close_pos < 0:
                    if strict:
                        raise LuaSyntaxError(f"Unterminated long string at offset {start}")
                    out.append(("symbol", ch, i, i + 1))
                    i += 1
                    continue
                end = close_pos + len(close_delim)
                out.append(("string", text[start:end], start, end))
                i = end
                continue

        # Short strings: "..." or '...'
        if ch in ('"', "'"):
            start = i
            quote = ch
            j = i + 1
            closed = False
            while j < n:
                c = text[j]
                if c == "\\":
                    if j + 1 >= n:
                        if strict:
                            raise LuaSyntaxError(f"Unterminated short string escape at offset {start}")
                        break
                    nxt = text[j + 1]
                    if nxt == "\r" and j + 2 < n and text[j + 2] == "\n":
                        j += 3
                    elif nxt == "z":
                        j += 2
                        while j < n and text[j] in " \t\r\n\f\v":
                            j += 1
                    else:
                        j += 2
                elif c == quote:
                    j += 1
                    closed = True
                    break
                elif c in ("\n", "\r"):
                    if strict:
                        raise LuaSyntaxError(f"Unterminated short string at offset {start}")
                    break
                else:
                    j += 1
            if not closed:
                if strict:
                    raise LuaSyntaxError(f"Unterminated short string at offset {start}")
                out.append(("symbol", ch, i, i + 1))
                i += 1
                continue
            out.append(("string", text[start:j], start, j))
            i = j
            continue

        # Identifiers / keywords
        if ch.isalpha() or ch == "_":
            m_id = _RE_IDENT.match(text, i)
            if m_id:
                end = m_id.end()
                out.append(("name", m_id.group(0), i, end))
                i = end
                continue

        # Multi-character Lua operators / delimiters
        matched_multi = False
        for sym in _MULTI_SYMBOLS:
            if text.startswith(sym, i):
                end = i + len(sym)
                out.append(("symbol", sym, i, end))
                i = end
                matched_multi = True
                break
        if matched_multi:
            continue

        # Single-character symbol (including digits for backward compatibility)
        out.append(("symbol", ch, i, i + 1))
        i += 1

    return out


def tokens(text: str) -> list[tuple[str, str, int, int]]:
    """Return non-whitespace, non-comment Lua tokens as `(kind, value, start, end)`."""
    return _scan_lua(text, strict=False, include_comments=False)


def lex_strict(text: str) -> list[tuple[str, str, int, int]]:
    """Strictly tokenize Lua source, raising LuaSyntaxError on unterminated literals."""
    return _scan_lua(text, strict=True, include_comments=False)


def line_comments(text: str) -> list[tuple[int, int, str]]:
    """Return Lua comment spans as `(start, end, raw_text)`."""
    return [
        (start, end, val)
        for kind, val, start, end in _scan_lua(text, strict=False, include_comments=True)
        if kind == "comment"
    ]


def lua_string(value: str) -> str:
    """Encode a Python string as a valid double-quoted Lua short string literal."""
    escapes = {
        "\\": "\\\\",
        '"': '\\"',
        "\a": "\\a",
        "\b": "\\b",
        "\f": "\\f",
        "\n": "\\n",
        "\r": "\\r",
        "\t": "\\t",
        "\v": "\\v",
    }
    parts = ['"']
    for ch in str(value):
        if ch in escapes:
            parts.append(escapes[ch])
        elif ord(ch) < 0x20 or ord(ch) == 0x7F:
            parts.append(f"\\x{ord(ch):02x}")
        else:
            parts.append(ch)
    parts.append('"')
    return "".join(parts)


def format_lua_key(key: str) -> str:
    """Serialize a Lua table key as a bare identifier when valid, otherwise bracketed."""
    if _RE_IDENT.fullmatch(key) and key not in LUA_KEYWORDS:
        return key
    return f"[{lua_string(key)}]"


def string_value(raw: str) -> str | None:
    """Decode a Lua short string or long-bracket string literal accurately."""
    s = raw.strip()
    if len(s) < 2:
        return None

    # Long-bracket string: [=[...]=]
    m_open = _RE_LONG_OPEN.match(s)
    if m_open:
        eqs = m_open.group(1)
        close_delim = f"]{eqs}]"
        if not s.endswith(close_delim) or len(s) < len(m_open.group(0)) + len(close_delim):
            return None
        body = s[m_open.end(): len(s) - len(close_delim)]
        if body.startswith("\r\n"):
            body = body[2:]
        elif body.startswith("\n"):
            body = body[1:]
        return body

    # Short string: "..." or '...'
    quote = s[0]
    if quote not in ('"', "'") or s[-1] != quote:
        return None

    inner = s[1:-1]
    out: list[str] = []
    i = 0
    n = len(inner)
    simple_escapes: dict[str, str] = {
        "a": "\a", "b": "\b", "f": "\f", "n": "\n", "r": "\r",
        "t": "\t", "v": "\v", "\\": "\\", '"': '"', "'": "'",
    }
    while i < n:
        ch = inner[i]
        if ch != "\\":
            out.append(ch)
            i += 1
            continue
        i += 1
        if i >= n:
            return None
        esc = inner[i]
        if esc in simple_escapes:
            out.append(simple_escapes[esc])
            i += 1
        elif esc == "\n":
            out.append("\n")
            i += 1
        elif esc == "\r":
            out.append("\n")
            i += 2 if (i + 1 < n and inner[i + 1] == "\n") else 1
        elif esc == "z":
            i += 1
            while i < n and inner[i] in " \t\r\n\f\v":
                i += 1
        elif esc == "x" and i + 2 < n:
            try:
                out.append(chr(int(inner[i + 1: i + 3], 16)))
                i += 3
            except ValueError:
                return None
        elif esc == "u" and i + 1 < n and inner[i + 1] == "{":
            close_u = inner.find("}", i + 2)
            if close_u < 0:
                return None
            try:
                codepoint = int(inner[i + 2: close_u], 16)
                out.append(chr(codepoint))
                i = close_u + 1
            except (ValueError, OverflowError):
                return None
        elif esc.isdigit():
            j = i
            while j < n and j < i + 3 and inner[j].isdigit():
                j += 1
            code = int(inner[i:j])
            if code > 255:
                return None
            out.append(chr(code))
            i = j
        else:
            out.append(esc)
            i += 1
    return "".join(out)


def calls(text: str, method: str) -> Iterator[tuple[int, int, int, int]]:
    """Yield `(call_start, call_end, inner_start, inner_end)` for `hl.<method>` calls.

    Supports both parenthesized calls (`hl.monitor({...})`) and parenthesis-free
    table calls (`hl.monitor {...}`), while rejecting chained accesses such as
    `other.hl.monitor(...)`.
    """
    ts = tokens(text)
    paren_stack: list[int] = []
    brace_stack: list[int] = []
    paren_ends: dict[int, int] = {}
    brace_ends: dict[int, int] = {}

    for idx, (kind, value, _, _) in enumerate(ts):
        if kind != "symbol":
            continue
        if value == "(":
            paren_stack.append(idx)
        elif value == ")" and paren_stack:
            paren_ends[paren_stack.pop()] = idx
        elif value == "{":
            brace_stack.append(idx)
        elif value == "}" and brace_stack:
            brace_ends[brace_stack.pop()] = idx

    limit = len(ts) - 3
    for i in range(limit):
        t0, t1, t2, t3 = ts[i], ts[i + 1], ts[i + 2], ts[i + 3]
        if (
            t0[0] == "name" and t0[1] == "hl"
            and t1[0] == "symbol" and t1[1] == "."
            and t2[0] == "name" and t2[1] == method
        ):
            if i > 0 and ts[i - 1][0] == "symbol" and ts[i - 1][1] in (".", ":"):
                continue
            if t3[0] == "symbol" and t3[1] == "(" and (i + 3) in paren_ends:
                end = paren_ends[i + 3]
                yield t0[2], ts[end][3], t3[3], ts[end][2]
            elif t3[0] == "symbol" and t3[1] == "{" and (i + 3) in brace_ends:
                end = brace_ends[i + 3]
                yield t0[2], ts[end][3], t3[2], ts[end][3]


def find_table_bounds(text: str, start: int = 0, end: int | None = None) -> tuple[int, int]:
    """Return `(open_pos, close_pos_exclusive)` of the first balanced `{...}` table."""
    slice_end = len(text) if end is None else end
    ts = tokens(text[start:slice_end])
    open_pos = -1
    depth = 0
    for kind, value, t_start, t_end in ts:
        if kind != "symbol":
            continue
        if value == "{":
            if depth == 0:
                open_pos = start + t_start
            depth += 1
        elif value == "}" and depth > 0:
            depth -= 1
            if depth == 0:
                return open_pos, start + t_end
    raise LuaSyntaxError("No balanced Lua table found in source span")


def table_entries(text: str, start: int, end: int) -> list[TableEntry]:
    """Parse all top-level entries inside the Lua table slice `text[start:end]`."""
    ts = tokens(text[start:end])
    if len(ts) < 2 or ts[0][1] != "{" or ts[-1][1] != "}":
        raise LuaSyntaxError("Expected a balanced Lua table constructor '{...}'")

    entries: list[TableEntry] = []
    n_toks = len(ts)
    i = 1  # token immediately after '{'

    while i < n_toks - 1:
        kind, value, t_start, t_end = ts[i]
        if kind == "symbol" and value in (",", ";"):
            i += 1
            continue
        if kind == "symbol" and value == "}":
            break

        entry_start = start + t_start
        key: str | None = None
        key_raw = ""
        key_start = entry_start
        key_end = entry_start

        # Case 1: bare identifier key -> name = value
        if (
            kind == "name"
            and value not in LUA_KEYWORDS
            and i + 1 < n_toks - 1
            and ts[i + 1][0] == "symbol"
            and ts[i + 1][1] == "="
        ):
            key = value
            key_raw = value
            key_start = start + t_start
            key_end = start + t_end
            i += 2

        # Case 2: bracketed key -> [expr] = value (["k"], ['k'], [ [[k]] ], [expr])
        elif kind == "symbol" and value == "[":
            m = i + 1
            k_depth = 1
            b_depth = 0
            p_depth = 0
            while m < n_toks - 1 and k_depth > 0:
                mk, mv, _, _ = ts[m]
                if mk == "symbol":
                    if mv == "[":
                        k_depth += 1
                    elif mv == "]":
                        k_depth -= 1
                        if k_depth == 0:
                            break
                    elif mv == "{":
                        b_depth += 1
                    elif mv == "}":
                        b_depth -= 1
                    elif mv == "(":
                        p_depth += 1
                    elif mv == ")":
                        p_depth -= 1
                m += 1

            if (
                k_depth == 0
                and m + 1 < n_toks - 1
                and ts[m + 1][0] == "symbol"
                and ts[m + 1][1] == "="
            ):
                key_start = start + t_start
                key_end = start + ts[m][3]
                key_raw = text[key_start:key_end]
                if m == i + 2 and ts[i + 1][0] == "string":
                    key = string_value(ts[i + 1][1])
                else:
                    key = None
                i = m + 2

        # Scan value expression tracking nested {}, (), [] and Lua block keywords
        first = i
        j = i
        depth = 0
        blocks = 0
        sep_pos: int | None = None
        sep_char: str | None = None

        while j < n_toks - 1:
            vk, vv, vs, _ = ts[j]
            if vk == "name" and (j == 0 or ts[j - 1][1] not in (".", ":")):
                if vv in ("function", "if", "do", "repeat"):
                    blocks += 1
                elif vv in ("end", "until") and blocks > 0:
                    blocks -= 1
            elif vk == "symbol":
                if depth == 0 and blocks == 0:
                    if vv in (",", ";"):
                        sep_pos = start + vs
                        sep_char = vv
                        break
                    if vv == "=":
                        raise LuaSyntaxError(
                            f"Unexpected '=' in value expression at offset {start + vs}; "
                            "missing separator between entries"
                        )
                if vv in ("{", "(", "["):
                    depth += 1
                elif vv in ("}", ")", "]") and depth > 0:
                    depth -= 1
            j += 1

        if j == first:
            raise LuaSyntaxError(f"Missing Lua value for entry '{key_raw or key or first}'")

        val_start = start + ts[first][2]
        val_end = start + ts[j - 1][3]
        entries.append(
            TableEntry(
                key=key,
                key_raw=key_raw,
                entry_start=entry_start,
                entry_end=val_end,
                key_start=key_start,
                key_end=key_end,
                value_start=val_start,
                value_end=val_end,
                sep_pos=sep_pos,
                sep_char=sep_char,
            )
        )
        i = (j + 1) if sep_pos is not None else j

    return entries


def fields(text: str, start: int, end: int) -> dict[str, tuple[int, int]]:
    """Return value spans for top-level string-keyed fields (bare or bracketed) of a table."""
    result: dict[str, tuple[int, int]] = {}
    for entry in table_entries(text, start, end):
        if entry.key is not None:
            result[entry.key] = (entry.value_start, entry.value_end)
    return result


def apply_edits(text: str, edits: list[tuple[int, int, str]]) -> str:
    """Apply non-overlapping `(start, end, replacement)` spans in reverse offset order."""
    if not edits:
        return text
    ordered = sorted(edits, key=lambda e: (e[0], e[1]), reverse=True)
    prev_start = len(text) + 1
    result = text
    for start, end, repl in ordered:
        if start < 0 or end < start or end > len(text) or end > prev_start:
            raise LuaSyntaxError(f"Overlapping edit span ({start}, {end}) vs {prev_start}")
        result = result[:start] + repl + result[end:]
        prev_start = start
    return result


def _leading_ws(s: str) -> str:
    idx = 0
    while idx < len(s) and s[idx] in (" ", "\t"):
        idx += 1
    return s[:idx]


def _infer_table_indent(text: str, open_brace: int, entries: list[TableEntry]) -> tuple[str, str, int | None]:
    """Infer `(block_indent, field_indent, eq_column)` for a multi-line Lua table."""
    open_line_start = text.rfind("\n", 0, open_brace) + 1
    block_indent = _leading_ws(text[open_line_start:open_brace])
    field_indent = block_indent + "    "

    eq_columns: list[int] = []
    has_padded_eq = False

    for entry in entries:
        e_line_start = text.rfind("\n", 0, entry.entry_start) + 1
        prefix = text[e_line_start:entry.entry_start]
        if prefix.strip(" \t") == "":
            field_indent = prefix
            if entry.key is not None and entry.key_raw == entry.key:
                eq_pos = text.find("=", entry.key_end, entry.value_start)
                if eq_pos >= 0:
                    col = eq_pos - entry.entry_start
                    eq_columns.append(col)
                    if eq_pos - entry.key_end > 1:
                        has_padded_eq = True

    aligned_col = eq_columns[0] if (has_padded_eq and eq_columns and len(set(eq_columns)) == 1) else None
    return block_indent, field_indent, aligned_col


def _format_multiline_entry(
    field_indent: str,
    aligned_col: int | None,
    key: str,
    raw: str,
) -> str:
    formatted_key = format_lua_key(key)
    if aligned_col is not None and formatted_key == key:
        pad = max(1, aligned_col - len(formatted_key))
        return f"{field_indent}{formatted_key}{' ' * pad}= {raw},"
    return f"{field_indent}{formatted_key} = {raw},"


def _plan_replace_field(
    text: str,
    open_brace: int,
    close_brace: int,
    key: str,
    raw: str,
) -> list[tuple[int, int, str]]:
    """Compute non-overlapping reverse-span edits to set `key = raw` inside `{...}`."""
    entries = table_entries(text, open_brace, close_brace + 1)
    matches = [e for e in entries if e.key == key]
    edits: list[tuple[int, int, str]] = []

    if matches:
        # Update last matching entry in place (Lua last-key-wins semantics)
        last_match = matches[-1]
        edits.append((last_match.value_start, last_match.value_end, raw))

        # Remove earlier duplicate entries cleanly without disturbing adjacent comments
        for dup in matches[:-1]:
            rm_start = dup.entry_start
            rm_end = (dup.sep_pos + 1) if dup.sep_pos is not None else dup.entry_end
            line_start = text.rfind("\n", 0, rm_start) + 1
            nl = text.find("\n", rm_end)
            line_end = (nl + 1) if nl != -1 else len(text)
            if (
                text[line_start:rm_start].strip(" \t") == ""
                and text[rm_end:line_end].strip(" \t\r\n") == ""
            ):
                edits.append((line_start, line_end, ""))
            else:
                if rm_end < close_brace and text[rm_end] == " ":
                    rm_end += 1
                edits.append((rm_start, rm_end, ""))
        return edits

    formatted_key = format_lua_key(key)
    is_multiline = "\n" in text[open_brace:close_brace]

    if not is_multiline:
        if not entries:
            interior = text[open_brace + 1: close_brace]
            if "--" in interior:
                edits.append((close_brace, close_brace, f" {formatted_key} = {raw} "))
            else:
                edits.append((open_brace + 1, close_brace, f" {formatted_key} = {raw} "))
            return edits
        last = entries[-1]
        if last.sep_pos is not None:
            sep = last.sep_char or ","
            edits.append((last.sep_pos + 1, last.sep_pos + 1, f" {formatted_key} = {raw}{sep}"))
        else:
            edits.append((last.value_end, last.value_end, f", {formatted_key} = {raw}"))
        return edits

    # Multi-line table insertion (never clobbers interior comments)
    block_indent, field_indent, aligned_col = _infer_table_indent(text, open_brace, entries)
    field_line = _format_multiline_entry(field_indent, aligned_col, key, raw)
    close_line_start = text.rfind("\n", open_brace, close_brace) + 1
    close_prefix_is_ws = text[close_line_start:close_brace].strip(" \t") == ""

    if not entries:
        if close_prefix_is_ws:
            edits.append((close_line_start, close_line_start, f"{field_line}\n"))
        else:
            edits.append((close_brace, close_brace, f"\n{field_line}\n{block_indent}"))
        return edits

    last = entries[-1]
    if close_prefix_is_ws:
        if last.sep_pos is None:
            # Insert separator strictly before trailing whitespace or inline '--' comment
            edits.append((last.value_end, last.value_end, ","))
        edits.append((close_line_start, close_line_start, f"{field_line}\n"))
    else:
        if last.sep_pos is None:
            if last.value_end == close_brace:
                edits.append((close_brace, close_brace, f",\n{field_line}\n{block_indent}"))
            else:
                edits.append((last.value_end, last.value_end, ","))
                edits.append((close_brace, close_brace, f"\n{field_line}\n{block_indent}"))
        else:
            edits.append((close_brace, close_brace, f"\n{field_line}\n{block_indent}"))
    return edits


def replace_field(text: str, key: str, raw: str) -> str:
    """Replace or insert `key = raw` inside the Lua table in `text` without corruption."""
    open_brace, end_excl = find_table_bounds(text)
    close_brace = end_excl - 1
    edits = _plan_replace_field(text, open_brace, close_brace, key, raw)
    return apply_edits(text, edits)


def replace_nested_field(text: str, section: str, key: str, raw: str) -> str:
    """Replace or insert `key = raw` inside subtable `section` of an outer Lua table."""
    outer_open, outer_end = find_table_bounds(text)
    outer_close = outer_end - 1
    outer_entries = table_entries(text, outer_open, outer_end)

    sec_matches = [e for e in outer_entries if e.key == section]
    if sec_matches:
        target = sec_matches[-1]
        val_slice = text[target.value_start:target.value_end]
        if val_slice.startswith("{") and val_slice.endswith("}"):
            sub_open, sub_end = find_table_bounds(text, target.value_start, target.value_end)
            edits = _plan_replace_field(text, sub_open, sub_end - 1, key, raw)
            return apply_edits(text, edits)

    formatted_key = format_lua_key(key)
    is_multiline = "\n" in text[outer_open:outer_close]
    if is_multiline:
        _, field_indent, _ = _infer_table_indent(text, outer_open, outer_entries)
        inner_indent = field_indent + "    "
        subtable_raw = f"{{\n{inner_indent}{formatted_key} = {raw},\n{field_indent}}}"
    else:
        subtable_raw = f"{{ {formatted_key} = {raw} }}"

    edits = _plan_replace_field(text, outer_open, outer_close, section, subtable_raw)
    return apply_edits(text, edits)


def _verify_balanced_delimiters(toks: list[tuple[str, str, int, int]]) -> None:
    pairs = {"{": "}", "(": ")", "[": "]"}
    closers = {"}", ")", "]"}
    stack: list[tuple[str, int]] = []
    for kind, value, start, _ in toks:
        if kind != "symbol":
            continue
        if value in pairs:
            stack.append((pairs[value], start))
        elif value in closers:
            if not stack or stack[-1][0] != value:
                raise LuaSyntaxError(f"Unmatched '{value}' at offset {start}")
            stack.pop()
    if stack:
        expected, pos = stack[-1]
        raise LuaSyntaxError(f"Unclosed delimiter (expected '{expected}' for offset {pos})")


def _validate_table_integrity(text: str, start: int, end: int, *, check_nested: bool = False) -> None:
    entries = table_entries(text, start, end)
    seen_keys: set[str] = set()
    for idx, entry in enumerate(entries):
        if idx < len(entries) - 1 and entry.sep_pos is None:
            raise LuaSyntaxError(
                f"Missing separator after entry '{entry.key_raw or entry.key or idx}' in Lua table"
            )
        if entry.key is not None:
            if entry.key in seen_keys:
                raise LuaSyntaxError(f"Duplicate Lua table key '{entry.key}'")
            seen_keys.add(entry.key)
        if check_nested:
            val_raw = text[entry.value_start:entry.value_end]
            if val_raw.startswith("{") and val_raw.endswith("}"):
                sub_open, sub_end = find_table_bounds(text, entry.value_start, entry.value_end)
                _validate_table_integrity(text, sub_open, sub_end, check_nested=False)


def _verify_native_lua_syntax(candidate: str) -> None:
    """Stage 2: zero-execution compile-only syntax check via luac, luajit, or lua."""
    luac = shutil.which("luac")
    if luac:
        cmd = [luac, "-p", "-"]
        tool = "luac"
    elif luajit := shutil.which("luajit"):
        cmd = [
            luajit, "-e",
            "local s=io.read('*a'); local f,e=loadstring(s,'monitors.lua'); "
            "if not f then io.stderr:write(tostring(e)..'\\n'); os.exit(1) end",
        ]
        tool = "luajit"
    elif lua := shutil.which("lua"):
        cmd = [
            lua, "-e",
            "local s=io.read('*a'); local f,e=load(s,'monitors.lua','t'); "
            "if not f then io.stderr:write(tostring(e)..'\\n'); os.exit(1) end",
        ]
        tool = "lua"
    else:
        return

    try:
        proc = subprocess.run(
            cmd,
            input=candidate,
            capture_output=True,
            text=True,
            timeout=3.0,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return

    if proc.returncode != 0:
        detail = proc.stderr.strip() or proc.stdout.strip() or f"exit {proc.returncode}"
        raise LuaSyntaxError(f"Native {tool} syntax check failed: {detail}")


def validate_lua_syntax(
    candidate: str,
    *,
    check_blocks: tuple[str, ...] = ("monitor", "workspace_rule", "config"),
    verify_native: bool = True,
) -> None:
    """Two-stage pre-write Lua gate: pure-Python AST check + native luac/luajit/lua check."""
    toks = lex_strict(candidate)
    _verify_balanced_delimiters(toks)

    for method in check_blocks:
        for _, _, inner_start, inner_end in calls(candidate, method):
            inner_slice = candidate[inner_start:inner_end]
            inner_toks = tokens(inner_slice)
            if not inner_toks or inner_toks[0][1] != "{":
                continue
            open_pos, close_end = find_table_bounds(candidate, inner_start, inner_end)
            _validate_table_integrity(
                candidate,
                open_pos,
                close_end,
                check_nested=(method == "config"),
            )

    if verify_native:
        _verify_native_lua_syntax(candidate)
