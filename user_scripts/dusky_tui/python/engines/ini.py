#!/usr/bin/env python3
"""Comment-preserving, batched INI edits and valueless configuration flags."""
import re
lazy import subprocess
lazy import threading
lazy from collections import Counter, defaultdict
lazy from pathlib import Path
lazy from typing import Any

from python.frontend.core_types import BaseEngine
from python.shared.config_io import atomic_write, line_value, privileged_atomic_write, read_text, split_lines


class IniConfigEngine(BaseEngine):
    _RE_SECTION = re.compile(r'^\s*\[(.*?)\]\s*(?:[#;].*)?$')
    _RE_KEY = re.compile(r'^([ \t]*)([#;]?)([ \t]*)([a-zA-Z0-9_.-]+)(?:([ \t]*=[ \t]*)(.*)|[ \t]*)$')
    _include_comments = False

    def __init__(self, config_path: str = '/etc/pacman.conf'):
        self.config_path = Path(config_path).expanduser().resolve()
        self.cache: dict[str, Any] = {}
        self._snapshot = None
        self._loaded = False
        self._lock = threading.RLock()

    @property
    def target_path(self) -> str:
        return str(self.config_path)

    def _parse(self, text: str, *, include_comments: bool | None = None) -> dict[str, Any]:
        include_comments = self._include_comments if include_comments is None else include_comments
        cache = {}
        scope = 'DEFAULT'
        active = set()
        for line in split_lines(text):
            if section := self._RE_SECTION.prefixmatch(line):
                scope = section[1].strip()
            elif match := self._RE_KEY.prefixmatch(line):
                _, comment, _, key, assignment, value = match.groups()
                full_key = f'{scope}/{key}'
                if comment and (not include_comments or full_key in active):
                    continue
                if assignment is not None:
                    value = value.strip()
                    if len(value) >= 2 and value.startswith('"') and value.endswith('"'):
                        value = value[1:-1]
                else:
                    value = True
                cache[full_key] = value
                if not comment:
                    active.add(full_key)
        return cache

    def load_state(self) -> dict[str, Any]:
        with self._lock:
            self.cache = {}
            self._loaded = False
            try:
                text, self._snapshot = read_text(self.config_path)
                self.cache = self._parse(text)
                self._loaded = True
            except (OSError, UnicodeError) as exc:
                print(f'Failed to read {self.config_path}: {exc}')
            return self.cache

    def write_value(self, target_key: str, target_scope: str, new_value: str, item_type: str = 'string') -> tuple[bool, str, str]:
        return self.write_batch([(target_key, target_scope, new_value, item_type)])

    def write_batch(self, changes: list[tuple[str, str, str, str]]) -> tuple[bool, str, str]:
        if not changes:
            return True, 'No pending changes.', ''
        with self._lock:
            try:
                return self._write_batch(changes)
            except PermissionError:
                # Let the frontend retry the engine under its existing auth flow.
                return False, 'AUTH_REQUIRED', ''
            except (OSError, UnicodeError, ValueError, subprocess.SubprocessError) as exc:
                return False, f'INI write failed: {exc}', ''

    def _write_batch(self, changes):
        text, snapshot = read_text(self.config_path)
        if self._loaded and snapshot != self._snapshot:
            return False, f'File {self.config_path.name} was modified externally. Reload required.', ''
        internal = {key: value for key, value in self.cache.items() if key.startswith('__')}
        pending = {}
        internal_changes = set()
        lookup = {key: str(value).strip() for key, _, value, _ in changes}
        for cached_key, cached_value in self.cache.items():
            lookup.setdefault(cached_key.rsplit('/', 1)[-1], str(cached_value).strip())
        for key, scope, value, item_type in changes:
            if scope.startswith('__'):
                internal[f'{scope}/{key}'] = value
                internal_changes.add((scope, key))
                continue
            if '{' in scope:
                scope = re.sub(r'\{([^{}]+)\}', lambda match: lookup.get(match[1], match[0]), scope)
                if '{' in scope or '=""' in scope or "=''" in scope:
                    value = '__DELETE__'
            scope = scope or 'DEFAULT'
            section = self._RE_SECTION.fullmatch(f'[{scope}]')
            if (not re.fullmatch(r'[a-zA-Z0-9_.-]+', key) or any(c in scope for c in '\r\n\0')
                    or section is None or section[1].strip() != scope):
                raise ValueError(f'Invalid INI key or scope: {scope}/{key}')
            pending[(scope, key)] = line_value(value, item_type)

        lines = split_lines(text)
        newline = '\r\n' if '\r\n' in text else '\n'
        assignments = Counter()
        valueless = 0
        for line in lines:
            if match := self._RE_KEY.prefixmatch(line.rstrip('\r\n')):
                if match[5] is not None:
                    assignments[match[5]] += 1
                elif not match[2]:
                    valueless += 1
        operator = assignments.most_common(1)[0][0] if assignments else '='
        flags = valueless > 0
        applied = set()
        output = []
        scope = 'DEFAULT'
        for line in lines:
            if section := self._RE_SECTION.prefixmatch(line.rstrip('\r\n')):
                scope = section[1].strip()
            elif match := self._RE_KEY.prefixmatch(line.rstrip('\r\n')):
                ws, comment, after_comment, key, assignment, old_value = match.groups()
                target = (scope, key)
                if target in pending:
                    value = pending[target]
                    disabled = target in applied or value == '__DELETE__' or (assignment is None and value == 'false')
                    applied.add(target)
                    if disabled:
                        output.append(line if comment else f'{ws}#{after_comment}{key}{assignment or ""}{old_value or ""}{newline}')
                    elif assignment is None and value == 'true':
                        output.append(f'{ws}{key}{newline}')
                    else:
                        output.append(f'{ws}{key}{assignment or operator}{value}{newline}')
                    continue
            output.append(line)

        missing = defaultdict(list)
        for target, value in pending.items():
            if target in applied or value == '__DELETE__' or (flags and value == 'false'):
                continue
            scope, key = target
            missing[scope].append(f'{key}{newline}' if flags and value == 'true' else f'{key}{operator}{value}{newline}')
        ends = {}
        scope = 'DEFAULT'
        for index, line in enumerate(output):
            if section := self._RE_SECTION.prefixmatch(line.rstrip('\r\n')):
                ends[scope] = index
                scope = section[1].strip()
        ends[scope] = len(output)
        for scope in sorted((scope for scope in missing if scope in ends), key=ends.get, reverse=True):
            index = ends[scope]
            if index and not output[index - 1].endswith(('\n', '\r')):
                output[index - 1] += newline
            output[index:index] = missing[scope]
        # Append new sections only after existing-scope insertions are complete.
        for scope in missing:
            if scope in ends:
                continue
            if output and not output[-1].endswith(('\n', '\r')):
                output[-1] += newline
            output.extend([newline, f'[{scope}]{newline}', *missing[scope]])
        result = ''.join(output)
        if result != text:
            try:
                self._snapshot = atomic_write(self.config_path, result, snapshot)
            except PermissionError:
                self._snapshot = privileged_atomic_write(self.config_path, result, snapshot)
        else:
            self._snapshot = snapshot
        self._loaded = True
        self.cache = self._parse(result)
        self.cache.update(internal)
        debug = ''
        reload_command = None
        if self.config_path.name == 'config' and self.config_path.parent.name == 'mako':
            reload_command = ['makoctl', 'reload']
        elif self.config_path.name == 'logind.conf' or self.config_path.parent.name == 'logind.conf.d':
            reload_command = ['systemctl', 'reload', 'systemd-logind.service']
        if result != text and reload_command is not None:
            try:
                reload = subprocess.run(reload_command, capture_output=True, text=True, encoding='utf-8', timeout=5)
                if reload.returncode:
                    debug = f'Configuration saved; reload failed: {reload.stderr.strip()}'
            except (OSError, subprocess.SubprocessError) as exc:
                debug = f'Configuration saved; reload failed: {exc}'
        return True, f'Successfully saved {len(pending) + len(internal_changes)} INI changes.', debug
