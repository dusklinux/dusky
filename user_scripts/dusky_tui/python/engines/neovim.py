"""Comment-preserving edits of deployed Neovim options and literal plugin fields."""
import os
import re
from dataclasses import dataclass
from pathlib import Path
from threading import RLock
lazy import json
lazy import subprocess

from python.frontend.core_types import BaseEngine
from python.shared.config_io import atomic_write, current_stamp, read_text

_TOKEN = re.compile(r'''"(?:\\.|[^"\\])*"|'(?:\\.|[^'\\])*'|[A-Za-z_][A-Za-z_0-9]*|\d+(?:\.\d+)?(?:[eE][+-]?\d+)?|[^\s]''')
_LONG = re.compile(r'\[(=*)\[')
_SCALAR = re.compile(r"""true|false|\d+(?:\.\d+)?(?:[eE][+-]?\d+)?|"(?:\\.|[^"\\])*"|'(?:\\.|[^'\\])*'""")

@dataclass(frozen=True, slots=True)
class Token:
    text: str
    start: int
    end: int


def tokens(text: str) -> list[Token]:
    """Tokenize scalars and punctuation, skipping Lua comments and long strings."""
    result = []
    pos = 0
    while pos < len(text):
        if text[pos].isspace():
            pos += 1
            continue
        comment = text.startswith('--', pos)
        start = pos + 2 if comment else pos
        long = _LONG.prefixmatch(text, start)
        if long:
            end_marker = ']' + long[1] + ']'
            end = text.find(end_marker, long.end())
            if end < 0:
                raise ValueError('Unterminated Lua long comment/string.')
            end += len(end_marker)
            if not comment:
                result.append(Token(text[pos:end], pos, end))
            pos = end
        elif comment:
            end = text.find('\n', pos)
            pos = len(text) if end < 0 else end
        else:
            match = _TOKEN.prefixmatch(text, pos)
            result.append(Token(match[0], pos, match.end()))
            pos = match.end()
    return result


def bindings(text: str) -> dict[str, list[Token | None]]:
    """Locate scalar assignments and named table fields without executing config."""
    stream = tokens(text)
    result = {}
    tables = []
    for i, token in enumerate(stream):
        value = token.text
        if value == '{':
            path = tables[-1] if tables else ()
            if i >= 2 and stream[i - 1].text == '=' and stream[i - 2].text.isidentifier():
                path = (*path, stream[i - 2].text)
            tables.append(path)
        elif value == '}':
            if tables:
                tables.pop()
        if value == '=' and i >= 5 and [t.text for t in stream[i - 5:i - 1]] in (
            ['vim', '.', 'opt', '.'], ['vim', '.', 'g', '.'],
        ):
            scope = 'options' if stream[i - 3].text == 'opt' else 'globals'
            result.setdefault(scope + '/' + stream[i - 1].text, []).append(None)
        if not _SCALAR.fullmatch(value):
            continue
        if i >= 6 and [t.text for t in stream[i - 6:i - 1]] in (
            ['vim', '.', 'opt', '.', stream[i - 2].text],
            ['vim', '.', 'g', '.', stream[i - 2].text],
        ) and stream[i - 1].text == '=':
            # An expression or second statement is not a scalar assignment.
            end = text.find('\n', token.end)
            tail = text[token.end:end if end >= 0 else len(text)].strip().removeprefix(';').strip()
            if tail and not tail.startswith('--'):
                continue
            scope = 'options' if stream[i - 4].text == 'opt' else 'globals'
            uid = scope + '/' + stream[i - 2].text
            result[uid][-1] = token
            continue
        elif tables and i >= 2 and stream[i - 1].text == '=' and stream[i - 2].text.isidentifier():
            if i + 1 < len(stream) and stream[i + 1].text not in {',', ';', '}'}:
                continue
            uid = '/'.join((*tables[-1], stream[i - 2].text))
        else:
            continue
        result.setdefault(uid, []).append(token)
    return result


_LUA_INSPECT = r'''
local ok, result = pcall(function()
  local request = vim.json.decode(vim.env._DUSKY_NVIM_REQUEST)
  assert(loadstring(request.content, '@' .. request.path))
  local state = vim.empty_dict()
  for key, expression in pairs(request.values) do
    local chunk = assert(loadstring('return ' .. expression))
    setfenv(chunk, {})
    state[key] = chunk()
  end
  for key, value in pairs(request.options or {}) do
    vim.api.nvim_set_option_value(key, value, {})
  end
  return state
end)
io.write(vim.json.encode({ok=ok, result=result}))
vim.cmd('qa!')
'''


def inspect_lua(path: Path, content: str, values: dict[str, str], options: dict | None = None) -> dict:
    payload = json.dumps({'path': str(path), 'content': content, 'values': values, 'options': options or {}})
    process = subprocess.run(
        ['nvim', '--headless', '-u', 'NONE', '-i', 'NONE', '--noplugin', '-c', 'lua ' + _LUA_INSPECT],
        env=os.environ | {'_DUSKY_NVIM_REQUEST': payload},
        capture_output=True, text=True, encoding='utf-8', timeout=10,
    )
    if process.returncode:
        raise ValueError(process.stderr.strip() or 'Neovim validation failed.')
    response = json.loads(process.stdout)
    if not response['ok']:
        raise ValueError(str(response['result']))
    return response['result']


def lua_value(value: str, item_type: str) -> tuple[str, object]:
    if item_type == 'bool':
        if value not in {'true', 'false'}:
            raise ValueError('Expected true or false.')
        return value, value == 'true'
    if item_type == 'int':
        number = int(value)
        if number < 0:
            raise ValueError('Expected a nonnegative integer.')
        return str(number), number
    if item_type == 'float':
        number = float(value)
        if not 0 <= number < float('inf'):
            raise ValueError('Expected a finite nonnegative number.')
        return repr(number), number
    if item_type not in {'string', 'cycle', 'picker', 'color'}:
        raise ValueError(f'Unsupported setting type: {item_type}')
    # JSON Unicode escapes are not Lua escapes. Preserve UTF-8 and escape controls.
    escaped = ''.join('\\' + char if char in {'"', '\\'} else f'\\{ord(char):03d}'
                      if ord(char) < 32 or ord(char) == 127 else char for char in value)
    return '"' + escaped + '"', value


class NeovimEngine(BaseEngine):
    def __init__(self, config_path: str, items=()):
        self.path = Path(config_path).expanduser().resolve()
        self._lock = RLock()
        self._text = None
        self._stamp = None
        self._bindings = {}
        self._rules = frozendict((f'{item.scope}/{item.key}', item) for item in items)

    @property
    def target_path(self) -> str:
        return str(self.path)

    def load_state(self) -> dict:
        with self._lock:
            text, stamp = read_text(self.path)
            if stamp is None:
                raise FileNotFoundError(f'Deployed configuration is missing: {self.path}')
            found = bindings(text)
            values = {key: entries[0].text for key, entries in found.items()
                      if len(entries) == 1 and entries[0] is not None}
            state = inspect_lua(self.path, text, values)
            if self.path.name == 'init.lua' and 'globals/dusky_bigfile_size' not in found:
                # The audited config defaults to 1 MiB; insert before bootstrap on write.
                state['globals/dusky_bigfile_size'] = 1024 * 1024
            self._text, self._stamp, self._bindings = text, stamp, found
            return dict(state)

    def write_value(self, target_key: str, target_scope: str, new_value: str,
                    item_type: str = 'string') -> tuple[bool, str, str]:
        return self.write_batch([(target_key, target_scope, new_value, item_type)])

    def write_batch(self, changes: list[tuple[str, str, str, str]]) -> tuple[bool, str, str]:
        with self._lock:
            try:
                if self._text is None:
                    self.load_state()
                if current_stamp(self.path) != self._stamp:
                    return False, 'Configuration changed externally. Press F5 to reload before saving.', ''
                if not changes:
                    return True, 'No pending changes.', ''
                text = self._text
                replacements = {}
                options = {}
                expected = {}
                for key, scope, value, item_type in changes:
                    uid = f'{scope}/{key}' if scope != 'DEFAULT' else key
                    literal, parsed = lua_value(value, item_type)
                    expected[uid] = parsed
                    if rule := self._rules.get(uid):
                        if rule.type_ != item_type:
                            raise ValueError(f'Incorrect setting type for {uid}.')
                        if item_type in {'cycle', 'picker'} and parsed not in rule.options:
                            raise ValueError(f'{uid}: choose one of {rule.options}.')
                        if item_type in {'int', 'float'}:
                            if rule.min_val is not None and parsed < rule.min_val:
                                raise ValueError(f'{uid}: minimum is {rule.min_val}.')
                            if rule.max_val is not None and parsed > rule.max_val:
                                raise ValueError(f'{uid}: maximum is {rule.max_val}.')
                    if uid == 'globals/dusky_bigfile_size' and (item_type != 'int' or parsed < 1):
                        raise ValueError('Large-file threshold must be a positive byte count.')
                    entries = self._bindings.get(uid, [])
                    if len(entries) == 1 and entries[0] is not None:
                        entry = entries[0]
                        replacements[(entry.start, entry.end)] = literal
                    elif uid not in self._bindings and uid == 'globals/dusky_bigfile_size' and self.path.name == 'init.lua':
                        stream = tokens(text)
                        matches = [token.start for i, token in enumerate(stream[:-3])
                                   if token.text == 'require'
                                   and [t.text for t in stream[i + 1:i + 4]] in (
                                       ['(', '"config.lazy"', ')'], ['(', "'config.lazy'", ')'])
                                   and not text[text.rfind('\n', 0, token.start) + 1:token.start].strip()]
                        if len(matches) != 1:
                            raise ValueError('Cannot locate an unambiguous Dusky Nvim bootstrap.')
                        pos = matches[0]
                        replacements[(pos, pos)] = f'vim.g.dusky_bigfile_size = {literal}\n'
                    else:
                        raise ValueError(f'{uid} is missing, complex, or ambiguous; edit this binding in Neovim.')
                    if scope == 'options':
                        options[key] = parsed
                for (start, end), literal in sorted(replacements.items(), reverse=True):
                    text = text[:start] + literal + text[end:]
                found = bindings(text)
                values = {key: entries[0].text for key, entries in found.items()
                          if len(entries) == 1 and entries[0] is not None}
                state = inspect_lua(self.path, text, values, options)
                if any(state.get(uid) != value for uid, value in expected.items()):
                    raise ValueError('Candidate settings did not round-trip; no file was saved.')
                stamp = atomic_write(self.path, text, self._stamp)
                self._text, self._stamp, self._bindings = text, stamp, found
                return True, f'Saved {len(changes)} setting(s). Applies to newly opened Neovim sessions.', ''
            except (OSError, ValueError, subprocess.SubprocessError) as exc:
                return False, str(exc), ''
