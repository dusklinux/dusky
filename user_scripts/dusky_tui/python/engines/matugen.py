#!/usr/bin/env python3
"""Toggle Matugen template blocks while preserving TOML strings and comments."""
import re
lazy import threading
lazy import tomllib
lazy from pathlib import Path
from typing import override
from python.frontend.core_types import BaseEngine
from python.shared.config_io import atomic_write, boolean, read_text, split_lines

type ChangeTuple = tuple[str, str, str, str]


class MatugenEngine(BaseEngine):
    _RE_HEADER = re.compile(r'^[ \t]*(#?)[ \t]*(\[.*\])[ \t]*(?:#.*)?$')
    _RE_UNCOMMENT = re.compile(r'^([ \t]*)#[ ]?')
    _RE_ASSIGNMENT = re.compile(r'''(?:[A-Za-z0-9_.-]+|'[^']*'|"(?:\\.|[^"])*")[ \t]*=''')

    def __init__(self, config_path: str | Path = '~/.config/matugen/config.toml') -> None:
        self.config_path = Path(config_path).expanduser().resolve()
        self.cache: dict[str, bool] = {}
        self._snapshot = None
        self._loaded = False
        self._lock = threading.RLock()

    @property
    @override
    def target_path(self) -> str:
        return str(self.config_path)

    @staticmethod
    def _scan_string(line: str, multiline: str, nesting: int) -> tuple[str, int]:
        """Track TOML quotes, ignoring comments and escaped basic-string quotes."""
        index = 0
        quote = ''
        while index < len(line):
            char = line[index]
            if multiline:
                if multiline == '"""' and char == '\\':
                    index += 2
                    continue
                if line.startswith(multiline, index):
                    index += 3
                    multiline = ''
                    continue
            elif quote:
                if quote == '"' and char == '\\':
                    index += 2
                    continue
                if char == quote:
                    quote = ''
            else:
                if char == '#':
                    break
                if line.startswith('"""', index) or line.startswith("'''", index):
                    multiline = line[index:index + 3]
                    index += 3
                    continue
                if char in "'\"":
                    quote = char
                elif char in '[{':
                    nesting += 1
                elif char in ']}':
                    nesting -= 1
            index += 1
        return multiline, nesting

    @classmethod
    def _blocks(cls, lines):
        headers = []
        body_lines = set()
        multiline = ''
        nesting = 0
        disabled = False
        for index, line in enumerate(lines):
            if not multiline and not nesting and (match := cls._RE_HEADER.prefixmatch(line.rstrip('\r\n'))):
                name = None
                try:
                    parsed = tomllib.loads(match[2] + '\nx = 0\n')
                    templates = parsed.get('templates')
                    if isinstance(templates, dict) and len(templates) == 1:
                        # Dotted names remain the same UI key as the source header.
                        parts = []
                        branch = templates
                        while isinstance(branch, dict) and len(branch) == 1:
                            key, value = next(iter(branch.items()))
                            if not isinstance(value, dict):
                                break
                            parts.append(key)
                            branch = value
                        if parts:
                            name = '.'.join(parts)
                except tomllib.TOMLDecodeError:
                    pass
                disabled = bool(match[1])
                headers.append((index, name, not disabled))
                continue
            content = cls._RE_UNCOMMENT.sub(r'\1', line, count=1) if disabled else line
            was_multiline = bool(multiline or nesting)
            stripped = content.strip()
            assignment = cls._RE_ASSIGNMENT.prefixmatch(stripped)
            if not disabled or was_multiline or assignment or stripped.startswith((']', '}')):
                multiline, nesting = cls._scan_string(content, multiline, nesting)
            if was_multiline or multiline or nesting:
                body_lines.add(index)
            elif disabled:
                if assignment or stripped.startswith((']', '}')):
                    body_lines.add(index)
            elif stripped and not stripped.startswith('#'):
                body_lines.add(index)
        blocks = {}
        for position, (start, name, active) in enumerate(headers):
            if name is None:
                continue
            if name in blocks:
                raise ValueError(f'Duplicate template block: {name}')
            end = headers[position + 1][0] if position + 1 < len(headers) else len(lines)
            # A blank after the last TOML body line separates subsequent prose.
            # Disabled bodies need assignment/string tracking: every source line
            # has an outer comment, including their multiline shell scripts.
            last_body = max((index for index in range(start + 1, end) if index in body_lines), default=start)
            for index in range(last_body + 1, end):
                if not lines[index].strip():
                    end = index
                    break
            blocks[name] = (start, end, active)
        return blocks

    @staticmethod
    def _state(blocks):
        return {alias: active for key, (_, _, active) in blocks.items() for alias in (key, f'DEFAULT/{key}')}

    @override
    def load_state(self) -> dict[str, bool]:
        with self._lock:
            self.cache = {}
            self._loaded = False
            try:
                text, self._snapshot = read_text(self.config_path)
                self.cache = self._state(self._blocks(split_lines(text)))
                self._loaded = True
            except (OSError, UnicodeError, ValueError) as exc:
                print(f'MatugenEngine: Failed to read {self.config_path}: {exc}')
            return self.cache

    @override
    def write_value(self, target_key: str, target_scope: str, new_value: str, item_type: str = 'bool') -> tuple[bool, str, str]:
        return self.write_batch([(target_key, target_scope, new_value, item_type)])

    @override
    def write_batch(self, changes: list[ChangeTuple]) -> tuple[bool, str, str]:
        if not changes:
            return True, 'No pending changes.', ''
        with self._lock:
            try:
                text, snapshot = read_text(self.config_path)
                if snapshot is None:
                    return False, f'Target configuration file {self.config_path} does not exist.', ''
                if self._loaded and snapshot != self._snapshot:
                    return False, f'File {self.config_path.name} was modified externally. Reload required.', ''
                lines = split_lines(text)
                blocks = self._blocks(lines)
                pending = {key.rsplit('/', 1)[-1]: boolean(value) for key, _, value, _ in changes}
                missing = pending.keys() - blocks.keys()
                if missing:
                    return False, f'Template(s) not found: {", ".join(sorted(missing))}', ''
                for key, enabled in pending.items():
                    start, end, active = blocks[key]
                    if enabled == active:
                        continue
                    for index in range(start, end):
                        if enabled:
                            lines[index] = self._RE_UNCOMMENT.sub(r'\1', lines[index], count=1)
                        elif lines[index].strip():
                            lines[index] = '# ' + lines[index]
                result = ''.join(lines)
                # Validate the active document before committing any block changes.
                tomllib.loads(result)
                self._snapshot = atomic_write(self.config_path, result, snapshot) if result != text else snapshot
                self._loaded = True
                self.cache = self._state(self._blocks(lines))
                return True, f'Successfully saved {len(pending)} template changes.', ''
            except (OSError, UnicodeError, ValueError) as exc:
                return False, f'Matugen write failed: {exc}', ''
