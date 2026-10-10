#!/usr/bin/env python3
"""Typed GSettings access with Gio and a command-line fallback.

The target path identifies the active user database for the TUI; it does not
select a backend. GSettings itself honors GSETTINGS_BACKEND and DCONF_PROFILE.
"""
import os
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Any
lazy import ast
lazy import math
lazy import subprocess
lazy import tempfile

from python.frontend.core_types import BaseEngine, ConfigItem


SCALAR_KINDS = frozendict({
    "b": "bool", "d": "float", "s": "string",
    **{signature: "int" for signature in ("i", "u", "n", "q", "x", "t", "y", "h")},
})
INTEGER_LIMITS = frozendict({
    "n": (-(2**15), 2**15 - 1), "q": (0, 2**16 - 1),
    "i": (-(2**31), 2**31 - 1), "u": (0, 2**32 - 1),
    "x": (-(2**63), 2**63 - 1), "t": (0, 2**64 - 1),
    "y": (0, 255), "h": (-(2**31), 2**31 - 1),
})


CURSOR_SCHEMA = "org.gnome.desktop.interface"
CURSOR_KEYS = frozenset(("cursor-theme", "cursor-size"))
_CLI_STRING_ESCAPES = frozendict(str.maketrans({
    "\\": "\\\\", "'": "\\'", "\n": "\\n", "\r": "\\r", "\t": "\\t",
}))


@dataclass(frozen=True, slots=True)
class SettingWriteResult:
    ok: bool
    message: str
    actual: str | None = None


class GSettingsEngine(BaseEngine):
    editable_target = False  # The database is watched, never opened as text.
    refresh_after_write = True  # Publish canonical backend values to the TUI.

    def __init__(
        self, config_path: str = "", schemas: list[str] | None = None,
        items: list[ConfigItem] | None = None,
    ) -> None:
        config_home = Path(os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config")
        self._config_path = Path(config_path).expanduser().resolve() if config_path else config_home / "dconf/user"
        self._items = items
        self._explicit_schemas = set(schemas or [])
        self.cache: dict[str, Any] = {}
        self._lock = threading.RLock()
        self._gio_available = self._check_gio_available()
        self.last_sync_error = ""

    @property
    def target_path(self) -> str:
        return str(self._config_path)

    @staticmethod
    def _check_gio_available() -> bool:
        try:
            import gi
            gi.require_version("Gio", "2.0")
            from gi.repository import Gio, GLib
            return True
        except (ImportError, ValueError):
            return False

    @staticmethod
    def _cli(*args: str) -> subprocess.CompletedProcess[str]:
        result = subprocess.run(
            ["gsettings", *args], capture_output=True, text=True,
            encoding="utf-8", timeout=5,
        )
        # dconf emits this untranslated warning when an asynchronous commit fails.
        # gsettings may still exit zero; preserve the caller's locale for defaults.
        if "failed to commit changes" in result.stderr.lower():
            raise RuntimeError(result.stderr.strip())
        return result

    @staticmethod
    def _schema(scope: str, key: str | None = None):
        from gi.repository import Gio
        if "\0" in scope or (key is not None and "\0" in key):
            raise ValueError("Schema and key must not contain NUL characters")
        source = Gio.SettingsSchemaSource.get_default()
        schema = source.lookup(scope, True) if source else None
        if not schema or not schema.get_path():
            raise ValueError(f"Unknown fixed schema: {scope}")
        if key is not None and not schema.has_key(key):
            raise ValueError(f"Unknown fixed-schema key: {scope}.{key}")
        return schema

    def get_installed_schemas(self) -> set[str]:
        if self._gio_available:
            from gi.repository import Gio
            source = Gio.SettingsSchemaSource.get_default()
            return set(source.list_schemas(True)[0]) if source else set()
        try:
            result = self._cli("list-schemas")
            return set(result.stdout.splitlines()) if result.returncode == 0 else set()
        except (OSError, subprocess.SubprocessError, RuntimeError):
            return set()

    def _get_target_schemas_and_keys(self) -> dict[str, set[str]]:
        result = {}
        for item in self._items or ():
            if item.scope and item.scope != "DEFAULT" and item.type_ not in {"action", "preset", "menu"}:
                result.setdefault(item.scope, set()).add(item.key)
        for identity in self.cache:
            if "/" in identity:
                scope, key = identity.split("/", 1)
                result.setdefault(scope, set()).add(key)
        for scope in self._explicit_schemas:
            result[scope] = set()  # Explicit schemas always mean every key.
        return result

    @staticmethod
    def _serialize(value: Any) -> str:
        if isinstance(value, bool):
            return "true" if value else "false"
        return str(value)

    @classmethod
    def _variant_text(cls, variant) -> str:
        return (cls._serialize(variant.unpack())
                if variant.get_type_string() in SCALAR_KINDS else variant.print_(True))

    @staticmethod
    def _decode_cli(value: str) -> str:
        value = value.strip()
        if value.startswith(("'", '"')):
            return ast.literal_eval(value)
        # Numeric annotations are emitted for unsigned/64-bit variants.
        head, sep, tail = value.partition(" ")
        if sep and head in {"byte", "int16", "uint16", "uint32", "int64", "uint64", "handle"}:
            return str(int(tail, 0))
        if sep and head == "double":
            return str(float(tail))
        # GVariant prints doubles with 17 digits; match Gio's Python formatting.
        if any(character in value for character in ".eE"):
            try:
                return str(float(value))
            except ValueError:
                pass  # Booleans and compound variant text remain unchanged.
        return value

    def load_state(self) -> dict[str, Any]:
        with self._lock:
            targets = self._get_target_schemas_and_keys()
            if not targets and self._items is not None:
                self.cache = {}
                return self.cache
            installed = self.get_installed_schemas()
            if self._items is None and not self._explicit_schemas:
                targets = {scope: set() for scope in sorted(installed)}
            state = {}
            if self._gio_available:
                from gi.repository import Gio
                for scope, keys in targets.items():
                    if scope not in installed:
                        continue
                    schema = self._schema(scope)
                    settings = Gio.Settings.new_full(schema, None, None)
                    for key in sorted(keys or schema.list_keys()):
                        if "\0" not in key and schema.has_key(key):
                            value = self._variant_text(settings.get_value(key))
                            state[f"{scope}/{key}"] = state[f"{scope}.{key}"] = value
            else:
                for scope, keys in targets.items():
                    if scope not in installed:
                        continue
                    if not keys:
                        try:
                            result = self._cli("list-keys", scope)
                            keys = set(result.stdout.splitlines()) if result.returncode == 0 else set()
                        except (OSError, subprocess.SubprocessError, RuntimeError):
                            continue
                    for key in sorted(keys):
                        try:
                            result = self._cli("get", scope, key)
                            if result.returncode == 0:
                                value = self._decode_cli(result.stdout)
                                state[f"{scope}/{key}"] = state[f"{scope}.{key}"] = value
                        except (OSError, subprocess.SubprocessError, RuntimeError, ValueError, SyntaxError):
                            continue
            self.cache = state
            return state

    @staticmethod
    def _validate_scalar(value: str, kind: str | None) -> Any:
        if kind == "bool":
            normalized = value.strip().lower()
            if normalized not in {"true", "false", "1", "0", "yes", "no", "on", "off", "t", "f", "y", "n"}:
                raise ValueError(f"Invalid boolean: {value!r}")
            return normalized in {"true", "1", "yes", "on", "t", "y"}
        if kind == "int":
            return int(value)
        if kind == "float":
            number = float(value)
            if not math.isfinite(number):
                raise ValueError("Value must be finite")
            return number
        return value  # Strings are literal: preserve whitespace and quotes.

    @staticmethod
    def _validate_input(scope: str, key: str, value: str) -> None:
        if any("\0" in text for text in (scope, key, value)):
            raise ValueError("Schema, key, and value must not contain NUL characters")
        if scope == CURSOR_SCHEMA:
            if key == "cursor-size" and int(value) <= 0:
                raise ValueError("Cursor size must be a positive integer")
            if key == "cursor-theme" and (not value or "\n" in value or "\r" in value):
                raise ValueError("Cursor theme must be nonempty and contain no line breaks")

    @classmethod
    def _variant_for_key(cls, scope: str, key: str, value: str) -> tuple[Any, Any]:
        """Validate a candidate without writing; also used by preset preparation."""
        from gi.repository import GLib
        cls._validate_input(scope, key, value)
        schema = cls._schema(scope, key)
        metadata = schema.get_key(key)
        vtype = metadata.get_value_type()
        signature = vtype.dup_string()
        kind = SCALAR_KINDS.get(signature)
        variant = (GLib.Variant(signature, cls._validate_scalar(value, kind))
                   if kind else GLib.Variant.parse(vtype, value, None, None))
        if not metadata.range_check(variant):
            raise ValueError(f"Value outside schema range: {scope}.{key}")
        return schema, variant

    def _write_single_setting(
        self, scope: str, key: str, value: str, item_type: str,
    ) -> SettingWriteResult:
        try:
            if self._gio_available:
                from gi.repository import Gio
                schema, variant = self._variant_for_key(scope, key, value)
                settings = Gio.Settings.new_full(schema, None, None)
                if not settings.is_writable(key):
                    raise ValueError(f"Setting is not writable: {scope}.{key}")
                expected = self._variant_text(variant)
                if not settings.set_value(key, variant):
                    raise RuntimeError(f"Backend rejected: {scope}.{key}")
                return SettingWriteResult(True, "Setting applied", expected)
            self._validate_input(scope, key, value)
            parsed = self._validate_scalar(value, item_type)
            if item_type in {"string", "cycle", "picker", "color"}:
                # GVariant string syntax, passed as one argv element (no shell).
                encoded = "'" + value.translate(_CLI_STRING_ESCAPES) + "'"
            else:
                encoded = self._serialize(parsed)
            result = self._cli("set", scope, key, encoded)
            if result.returncode:
                raise RuntimeError(result.stderr.strip() or "GSettings write failed")
            return SettingWriteResult(True, "Setting applied", self._serialize(parsed))
        except Exception as exc:
            return SettingWriteResult(False, f"{scope}.{key}: {exc}")

    def write_batch_results(
        self, changes: list[tuple[str, str, str, str]],
    ) -> dict[tuple[str, str], SettingWriteResult]:
        if not changes:
            return {}
        with self._lock:
            installed = self.get_installed_schemas()
            results = {}
            cursor_changed = False
            for key, scope, value, kind in changes:
                if not scope or scope == "DEFAULT":
                    result = SettingWriteResult(False, f"Missing GSettings schema scope for key '{key}'")
                elif scope not in installed:
                    result = SettingWriteResult(False, f"Schema '{scope}' is not installed on this system")
                else:
                    result = self._write_single_setting(scope, key, str(value), kind)
                if result.ok:
                    if not self._gio_available:
                        self.cache[f"{scope}/{key}"] = self.cache[f"{scope}.{key}"] = result.actual
                    cursor_changed |= scope == CURSOR_SCHEMA and key in CURSOR_KEYS
                results[(key, scope)] = result
            if self._gio_available and any(result.ok for result in results.values()):
                from gi.repository import Gio
                Gio.Settings.sync()
                settings_by_scope = {}
                for (key, scope), result in list(results.items()):
                    if not result.ok:
                        continue
                    try:
                        if scope not in settings_by_scope:
                            schema = self._schema(scope)
                            settings_by_scope[scope] = Gio.Settings.new_full(schema, None, None)
                        settings = settings_by_scope[scope]
                        actual = self._variant_text(settings.get_value(key))
                        override = settings.get_user_value(key)
                        self.cache[f"{scope}/{key}"] = self.cache[f"{scope}.{key}"] = actual
                        if (actual != result.actual or override is None
                                or self._variant_text(override) != result.actual):
                            results[(key, scope)] = SettingWriteResult(
                                False, f"Backend did not retain the requested override: {scope}.{key}", actual,
                            )
                    except Exception as exc:
                        results[(key, scope)] = SettingWriteResult(
                            False, f"Could not verify {scope}.{key}: {exc}",
                        )
                cursor_changed = any(
                    result.ok and scope == CURSOR_SCHEMA
                    and key in CURSOR_KEYS
                    for (key, scope), result in results.items()
                )
            if cursor_changed and not self.sync_cursor_wayland():
                for identity, result in list(results.items()):
                    if (result.ok and identity[1] == CURSOR_SCHEMA
                            and identity[0] in CURSOR_KEYS):
                        results[identity] = SettingWriteResult(
                            False, f"Setting saved; cursor sync failed: {self.last_sync_error}",
                            result.actual,
                        )
            return results

    def write_batch(self, changes: list[tuple[str, str, str, str]]) -> tuple[bool, str, str]:
        results = self.write_batch_results(changes)
        errors = [r.message for r in results.values() if not r.ok]
        return not errors, "\n".join(errors) if errors else f"Applied {len(results)} setting(s).", "\n".join(errors)

    def write_value(
        self, target_key: str, target_scope: str, new_value: str,
        item_type: str = "string",
    ) -> tuple[bool, str, str]:
        return self.write_batch([(target_key, target_scope, new_value, item_type)])

    def reset_key(self, scope: str, key: str) -> tuple[bool, str]:
        with self._lock:
            try:
                if self._gio_available:
                    from gi.repository import Gio
                    schema = self._schema(scope, key)
                    settings = Gio.Settings.new_full(schema, None, None)
                    if not settings.is_writable(key):
                        return False, f"Setting is not writable: {scope}.{key}"
                    settings.reset(key)
                    Gio.Settings.sync()
                    if settings.get_user_value(key) is not None:
                        return False, f"Backend did not remove the user override: {scope}.{key}"
                    actual = self._variant_text(settings.get_value(key))
                else:
                    if "\0" in scope or "\0" in key:
                        raise ValueError("Schema and key must not contain NUL characters")
                    result = self._cli("reset", scope, key)
                    if result.returncode:
                        return False, result.stderr.strip() or f"Could not reset {scope}.{key}"
                    result = self._cli("get", scope, key)
                    if result.returncode:
                        return False, result.stderr.strip() or f"Key reset but could not read {scope}.{key}"
                    actual = self._decode_cli(result.stdout)
                self.cache[f"{scope}/{key}"] = self.cache[f"{scope}.{key}"] = actual
                if scope == CURSOR_SCHEMA and key in CURSOR_KEYS:
                    if not self.sync_cursor_wayland():
                        return False, f"Setting reset; cursor sync failed: {self.last_sync_error}"
                return True, ""
            except Exception as exc:
                return False, str(exc)

    def sync_cursor_wayland(self, theme: str | None = None, size: int | None = None) -> bool:
        with self._lock:
            self.last_sync_error = ""
            try:
                scope = CURSOR_SCHEMA
                if theme is None or size is None:
                    if self._gio_available:
                        from gi.repository import Gio
                        schema = self._schema(scope)
                        for key in ("cursor-theme", "cursor-size"):
                            if not schema.has_key(key):
                                raise ValueError(f"Missing cursor setting: {scope}.{key}")
                        settings = Gio.Settings.new_full(schema, None, None)
                        theme = settings.get_string("cursor-theme") if theme is None else theme
                        size = settings.get_int("cursor-size") if size is None else size
                    else:
                        if theme is None:
                            result = self._cli("get", scope, "cursor-theme")
                            if result.returncode:
                                raise RuntimeError(result.stderr.strip())
                            theme = self._decode_cli(result.stdout)
                        if size is None:
                            result = self._cli("get", scope, "cursor-size")
                            if result.returncode:
                                raise RuntimeError(result.stderr.strip())
                            size = int(result.stdout.strip())
                if (not isinstance(theme, str) or not theme
                        or any(c in theme for c in "\n\r\0")
                        or type(size) is not int or not 0 < size <= INTEGER_LIMITS["i"][1]):
                    raise ValueError("Cursor theme must be nonempty and size must be a positive 32-bit integer")
                errors = []
                if os.environ.get("HYPRLAND_INSTANCE_SIGNATURE"):
                    try:
                        result = subprocess.run(
                            ["hyprctl", "setcursor", theme, str(size)],
                            capture_output=True, text=True, encoding="utf-8", timeout=5,
                        )
                        if result.returncode or result.stdout.strip() != "ok":
                            errors.append(
                                result.stderr.strip() or result.stdout.strip()
                                or "hyprctl returned no success response"
                            )
                    except (OSError, subprocess.SubprocessError) as exc:
                        errors.append(str(exc))
                directory = Path.home() / ".icons/default"
                try:
                    directory.mkdir(parents=True, exist_ok=True)
                    temporary = None
                    try:
                        with tempfile.NamedTemporaryFile(
                            mode="w", encoding="utf-8", dir=directory,
                            prefix=".index.theme.", delete=False,
                        ) as stream:
                            temporary = Path(stream.name)
                            stream.write(f"[Icon Theme]\nInherits={theme}\n")
                        temporary.chmod(0o644)
                        temporary.replace(directory / "index.theme")
                    finally:
                        if temporary is not None:
                            temporary.unlink(missing_ok=True)
                except OSError as exc:
                    errors.append(str(exc))
                self.last_sync_error = "; ".join(errors)
                return not errors
            except Exception as exc:
                self.last_sync_error = str(exc)
                return False
