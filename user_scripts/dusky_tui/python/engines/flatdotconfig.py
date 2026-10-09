#!/usr/bin/env python3
"""Space-delimited GPU Screen Recorder configuration with indexed duplicates."""
lazy import threading
lazy from pathlib import Path
lazy from typing import Any
from python.frontend.core_types import BaseEngine
from python.shared.config_io import atomic_write, line_value, read_text, split_lines


class FlatDotConfigEngine(BaseEngine):
    def __init__(self, config_path: str = '~/.config/gpu-screen-recorder/config_ui'):
        self.config_path = Path(config_path).expanduser().resolve()
        self.cache: dict[str, Any] = {}
        self._snapshot = None
        self._loaded = False
        self._lock = threading.RLock()

    @property
    def target_path(self) -> str:
        return str(self.config_path)

    @staticmethod
    def _parse(text):
        cache = {}
        counts = {}
        for line in split_lines(text):
            line = line.rstrip("\r\n")
            if not line or line.startswith('#'):
                continue
            raw_key, _, value = line.partition(' ')
            if not raw_key:
                continue
            count = counts[raw_key] = counts.get(raw_key, 0) + 1
            parts = raw_key.split('.')
            bindings = [(".".join(parts[:i]), ".".join(parts[i:])) for i in range(1, len(parts))] if len(parts) > 1 else [('DEFAULT', raw_key)]
            for scope, key in bindings:
                if count == 1:
                    cache[f'{scope}/{key}'] = value
                cache[f'{scope}/{key}:{count}'] = value
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
                text, snapshot = read_text(self.config_path)
                if self._loaded and snapshot != self._snapshot:
                    return False, f'File {self.config_path.name} was modified externally. Reload required.', ''
                pending = {}
                for key, scope, value, item_type in changes:
                    base, sep, index = key.rpartition(':')
                    occurrence = int(index) if sep and index.isdecimal() else 1
                    if sep and index.isdecimal():
                        key = base
                    if occurrence < 1:
                        raise ValueError('Duplicate indices must be positive.')
                    raw_key = f'{scope}.{key}' if scope and scope != 'DEFAULT' else key
                    if not raw_key or raw_key.startswith('#') or any(c.isspace() or c in '\0:' for c in raw_key):
                        raise ValueError(f'Invalid configuration key: {raw_key!r}')
                    pending[(raw_key, occurrence)] = line_value(value, item_type)
                counts = {}
                applied = set()
                output = []
                newline = '\r\n' if '\r\n' in text else '\n'
                for line in split_lines(text):
                    raw_key = line.rstrip('\r\n').partition(' ')[0]
                    if not raw_key or raw_key.startswith('#'):
                        output.append(line)
                        continue
                    count = counts[raw_key] = counts.get(raw_key, 0) + 1
                    target = (raw_key, count)
                    if target in pending:
                        applied.add(target)
                        value = pending[target]
                        if value != '__DELETE__':
                            output.append(f'{raw_key} {value}{newline}')
                    else:
                        output.append(line)
                for target, value in pending.items():
                    if target in applied or value == '__DELETE__':
                        continue
                    raw_key, occurrence = target
                    # Never turn an absent :N target into a different occurrence.
                    next_count = counts.get(raw_key, 0) + 1
                    if occurrence != next_count:
                        raise ValueError(f'Cannot append {raw_key}:{occurrence}; next occurrence is {next_count}.')
                    counts[raw_key] = next_count
                    if output and not output[-1].endswith(('\n', '\r')):
                        output[-1] += newline
                    output.append(f'{raw_key} {value}{newline}')
                result = ''.join(output)
                self._snapshot = atomic_write(self.config_path, result, snapshot) if result != text else snapshot
                self._loaded = True
                self.cache = self._parse(result)
                return True, f'Successfully saved {len(pending)} config_ui changes.', ''
            except (OSError, UnicodeError, ValueError) as exc:
                return False, f'Config write failed: {exc}', ''
