#!/usr/bin/env python3
"""Comment-preserving edits of single-line shell environment assignments.

Values are displayed without shell quoting, without evaluating expansions.
The __VAR__ prefix explicitly requests expansion when writing a shell value.
"""
import re
lazy import threading
lazy import subprocess
lazy from pathlib import Path
lazy from typing import Any
from python.frontend.core_types import BaseEngine
from python.shared.config_io import atomic_write, privileged_atomic_write, read_text, split_lines, line_value


class ShellEnvEngine(BaseEngine):
    _RE_ENV = re.compile(r'^([ \t]*)(export[ \t]+)?([A-Za-z_][A-Za-z0-9_]*)=(.*)$')

    def __init__(self, config_path: str = '/etc/locale.conf'):
        self.config_path = Path(config_path).expanduser().resolve()
        self.cache: dict[str, Any] = {}
        self._snapshot = None
        self._loaded = False
        self.file_mtime_ns = 0
        self._lock = threading.RLock()

    @property
    def target_path(self) -> str:
        return str(self.config_path)

    @staticmethod
    def _parse_value(raw_val):
        quote = ''
        index = value_end = 0
        word_boundary = False  # KEY= already starts the shell word.
        while index < len(raw_val):
            char = raw_val[index]
            if char == '\\' and quote != "'":
                index = min(index + 2, len(raw_val))
                value_end = index  # An escaped trailing blank belongs to the value.
                word_boundary = False
                continue
            if quote:
                if char == quote:
                    quote = ''
                value_end = index + 1
                word_boundary = False
            elif char in "\"'":
                quote = char
                value_end = index + 1
                word_boundary = False
            elif char == '#' and word_boundary:
                return raw_val[:value_end].lstrip(' \t'), raw_val[index:]
            elif char in ' \t':
                word_boundary = True
            else:
                value_end = index + 1
                word_boundary = False
            index += 1
        return raw_val[:value_end].lstrip(' \t'), ''

    @staticmethod
    def _decode(value):
        # Shell quote removal only: do not expand $variables or run substitutions.
        output, quote, index = [], '', 0
        while index < len(value):
            char = value[index]
            if char == '\\' and quote != "'" and index + 1 == len(value):
                raise ValueError('Multiline shell assignments require editing their source directly.')
            if char == '\\' and quote != "'" and index + 1 < len(value):
                following = value[index+1]
                if not quote or following in '$`"\\\n':
                    output.append(following)
                    index += 2
                    continue
            if quote:
                if char == quote:
                    quote = ''
                else:
                    output.append(char)
            elif char in "\"'":
                quote = char
            else:
                output.append(char)
            index += 1
        if quote:
            raise ValueError('Multiline or unterminated shell assignments require editing their source directly.')
        return ''.join(output)

    @classmethod
    def _parse(cls, text):
        cache, counts = {}, {}
        for line in split_lines(text):
            if match := cls._RE_ENV.fullmatch(line.rstrip('\r\n')):
                key, raw = match[3], match[4]
                count = counts[key] = counts.get(key, 0) + 1
                value = cls._decode(cls._parse_value(raw)[0])
                if count == 1:
                    cache[f'DEFAULT/{key}'] = value
                cache[f'DEFAULT/{key}:{count}'] = value
        return cache

    def load_state(self) -> dict[str, Any]:
        with self._lock:
            self.cache, self._loaded = {}, False
            try:
                text, self._snapshot = read_text(self.config_path)
                self.cache = self._parse(text)
                self._loaded = True
                self.file_mtime_ns = self._snapshot[3] if self._snapshot else 0
            except (OSError, UnicodeError, ValueError) as exc:
                print(f'Failed to read {self.config_path}: {exc}')
            return self.cache

    @staticmethod
    def _encode(value, old, expand):
        if old.startswith("'") and old.endswith("'") and not expand:
            return "'" + value.replace("'", "'\\''") + "'"
        if re.fullmatch(r'[A-Za-z0-9_./:@%+,=-]*', value) and not old.startswith('"'):
            return value
        escaped = value.replace('\\', '\\\\').replace('"', '\\"')
        if not expand:
            escaped = escaped.replace('$', '\\$').replace('`', '\\`')
        return '"' + escaped + '"'

    def write_value(self, target_key: str, target_scope: str, new_value: str, item_type: str = 'string') -> tuple[bool, str, str]:
        return self.write_batch([(target_key, target_scope, new_value, item_type)])

    def write_batch(self, changes: list[tuple[str, str, str, str]]) -> tuple[bool, str, str]:
        if not changes:
            return True, 'No pending changes.', ''
        with self._lock:
            try:
                text, snapshot = read_text(self.config_path)
                if self._loaded and snapshot != self._snapshot:
                    return False, 'Configuration modified externally. Reload required.', ''
                self._parse(text)  # Validate the single-line source before any commit.
                pending = {}
                for key, scope, value, kind in changes:
                    base, sep, index = key.rpartition(':')
                    occurrence = int(index) if sep and index.isdecimal() else 1
                    key = base if sep and index.isdecimal() else key
                    if scope not in ('', 'DEFAULT') or occurrence < 1 or not re.fullmatch(r'[A-Za-z_][A-Za-z0-9_]*', key):
                        raise ValueError(f'Invalid environment binding: {scope}/{key}')
                    pending[key, occurrence] = (line_value(value, kind), str(value).startswith('__VAR__'))
                newline = '\r\n' if '\r\n' in text else '\n'
                output, counts, applied = [], {}, set()
                for line in split_lines(text):
                    match = self._RE_ENV.fullmatch(line.rstrip('\r\n'))
                    if match is None:
                        output.append(line)
                        continue
                    ws, export, key, raw = match.groups()
                    count = counts[key] = counts.get(key, 0) + 1
                    target = key, count
                    if target not in pending:
                        output.append(line)
                        continue
                    applied.add(target)
                    value, expand = pending[target]
                    if value == '__DELETE__':
                        output.append(ws + '#' + line[len(ws):])
                    else:
                        old, comment = self._parse_value(raw)
                        output.append(f'{ws}{export or ""}{key}={self._encode(value, old, expand)}' + (' '+comment if comment else '') + newline)
                for target, (value, expand) in pending.items():
                    if target in applied or value == '__DELETE__':
                        continue
                    key, occurrence = target
                    next_count = counts.get(key, 0) + 1
                    if occurrence != next_count:
                        raise ValueError(f'Cannot append {key}:{occurrence}; next occurrence is {next_count}.')
                    counts[key] = next_count
                    if output and not output[-1].endswith('\n'):
                        output[-1] += newline
                    output.append(f'{key}={self._encode(value, "", expand)}{newline}')
                result = ''.join(output)
                if result != text:
                    try:
                        snapshot = atomic_write(self.config_path, result, snapshot)
                    except PermissionError:
                        snapshot = privileged_atomic_write(self.config_path, result, snapshot)
                self._snapshot, self._loaded = snapshot, True
                self.file_mtime_ns = snapshot[3] if snapshot else 0
                self.cache = self._parse(result)
                return True, f'Saved {len(pending)} environment changes.', ''
            except PermissionError:
                return False, 'AUTH_REQUIRED', ''
            except (OSError, UnicodeError, ValueError, subprocess.SubprocessError) as exc:
                return False, f'Environment write failed: {exc}', ''
