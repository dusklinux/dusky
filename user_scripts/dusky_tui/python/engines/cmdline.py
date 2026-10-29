"""Indexed kernel command-line edits with optimistic atomic commits."""
import re
lazy import threading
lazy import subprocess
lazy from pathlib import Path
lazy from typing import Any
from python.frontend.core_types import BaseEngine
from python.shared.config_io import atomic_write, privileged_atomic_write, read_text, boolean

# Sentinel object to safely detect omitted defaults in overridden dict methods
_sentinel = sentinel("_sentinel")

class BridgedStateDict(dict):
    """
    A bridged dictionary that prevents the TUI from marking optional parameters 
    as '[Missing]' and striking them out.
    
    Because kernel parameters are flags that are inherently 'optional' (their absence 
    just implies the kernel's compile-time behavior), they should always be treated as 
    available and fully editable by the UI.
    """
    def __contains__(self, key: Any) -> bool:
        return True

    def __getitem__(self, key: Any) -> Any:
        return super().get(key, "unset")

    def get(self, key: Any, default: Any = _sentinel) -> Any:
        # Respect the caller's explicit default if provided, otherwise fallback to "unset"
        if default is _sentinel:
            return super().get(key, "unset")
        return super().get(key, default)


class CmdlineEngine(BaseEngine):
    # Preserve quoted tokens and all intervening whitespace.
    _TOKEN = re.compile(r'((?:[^\s"\']|"[^"]*"|\'[^\']*\')+)')

    def __init__(self, config_path: str = '/etc/kernel/cmdline'):
        self.config_path = Path(config_path).expanduser().resolve()
        self.cache = BridgedStateDict()
        self._snapshot = None
        self._loaded = False
        self._lock = threading.RLock()
        self.file_mtime_ns = 0

    @property
    def target_path(self) -> str:
        return str(self.config_path)

    @classmethod
    def _parse(cls, text):
        cache, counts = BridgedStateDict(), {}
        for token in cls._TOKEN.split(text):
            if not token.strip():
                continue
            if token == '--':
                break  # The remaining arguments belong to init.
            key, sep, value = token.partition('=')
            count = counts[key] = counts.get(key, 0) + 1
            cache[f'DEFAULT/{key}:{count}'] = value if sep else 'true'
            # The base binding addresses the last occurrence; precedence itself
            # depends on the particular kernel parameter.
            cache[f'DEFAULT/{key}'] = value if sep else 'true'
        return cache

    def load_state(self) -> dict[str, Any]:
        with self._lock:
            self.cache = BridgedStateDict()
            self._loaded = False
            try:
                text, self._snapshot = read_text(self.config_path)
                self.cache = self._parse(text)
                self._loaded = True
                self.file_mtime_ns = self._snapshot[3] if self._snapshot else 0
            except (OSError, UnicodeError) as exc:
                print(f'Failed to read {self.config_path}: {exc}')
            return self.cache

    @classmethod
    def transform(cls, content, changes):
        tokens = cls._TOKEN.split(content.strip().replace('\n', ' '))
        # Never edit or append kernel parameters after the init separator.
        boundary = next((i for i, token in enumerate(tokens) if token == '--'), len(tokens))
        tail = tokens[boundary:]
        tokens = tokens[:boundary]
        counts, maximum = {}, {}
        for token in tokens:
            if token.strip():
                key = token.partition('=')[0]
                maximum[key] = maximum.get(key, 0) + 1
        pending = {}
        for key, scope, value, kind in changes:
            if scope not in ('', 'DEFAULT'):
                raise ValueError(f'Invalid kernel parameter scope: {scope}')
            base, sep, index = key.rpartition(':')
            occurrence = int(index) if sep and index.isdecimal() else None
            key = base if occurrence is not None else key
            if not key or key == '--' or any(c.isspace() or c in '=\0"\':' for c in key):
                raise ValueError(f'Invalid kernel parameter: {key!r}')
            if occurrence is not None and occurrence < 1:
                raise ValueError('Duplicate indices must be positive.')
            value = '' if value is None else str(value)
            if any(c in value for c in '\r\n\0'):
                raise ValueError('Kernel values cannot contain newlines or NUL.')
            delete = value in ('__DELETE__', 'nil', 'unset', '') or (kind == 'bool' and not boolean(value))
            if not delete and kind != 'bool':
                # Accept prequoted UI values; quote plain values containing spaces.
                if not (value.startswith('"') and value.endswith('"')):
                    if '"' in value:
                        raise ValueError('Kernel values cannot contain embedded double quotes.')
                    if any(c.isspace() for c in value):
                        value = '"' + value + '"'
                if len([t for t in cls._TOKEN.split(key+'='+value) if t.strip()]) != 1:
                    raise ValueError('Invalid quoted kernel value.')
            if occurrence is None:
                occurrence = maximum.get(key, 1)
            pending[(key, occurrence)] = None if delete else key if kind == 'bool' else key+'='+value
        output, applied = [], set()
        for token in tokens:
            if not token.strip():
                output.append(token)
                continue
            key = token.partition('=')[0]
            count = counts[key] = counts.get(key, 0) + 1
            target = key, count
            if target not in pending:
                output.append(token)
            else:
                applied.add(target)
                if pending[target] is not None:
                    output.append(pending[target])
        for target, replacement in pending.items():
            if target in applied or replacement is None:
                continue
            key, occurrence = target
            next_count = maximum.get(key, 0) + 1
            if occurrence != next_count:
                raise ValueError(f'Cannot append {key}:{occurrence}; next occurrence is {next_count}.')
            maximum[key] = next_count
            output.extend([' ', replacement])
        result = ''.join(output).strip()
        if tail:
            result += (' ' if result else '') + ''.join(tail).strip()
        return result + '\n'

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
                result = self.transform(text, changes)
                if result != text:
                    try:
                        snapshot = atomic_write(self.config_path, result, snapshot)
                    except PermissionError:
                        snapshot = privileged_atomic_write(self.config_path, result, snapshot)
                self._snapshot, self._loaded = snapshot, True
                self.file_mtime_ns = snapshot[3] if snapshot else 0
                self.cache = self._parse(result)
                return True, f'Saved {len(changes)} kernel parameter changes.', ''
            except PermissionError:
                return False, 'AUTH_REQUIRED', ''
            except (OSError, UnicodeError, ValueError, subprocess.SubprocessError) as exc:
                return False, f'Kernel parameter write failed: {exc}', ''
