#!/usr/bin/env python3
"""Dusky Sites configuration engine for Dusky TUI (Python 3.15+)."""

import os
import re
lazy import json
lazy import fcntl
lazy import tempfile
lazy import threading
lazy from pathlib import Path
lazy from typing import Any

from python.frontend.core_types import BaseEngine

# Match the native host's document headers and domain/url precedence.
_DOCUMENT_RE = re.compile(r"@-moz-document\s+([^{]+)\{", re.IGNORECASE)
_DOMAIN_RE = re.compile(r"""domain\(\s*["']?([^"')\s]+)["']?\s*\)""", re.IGNORECASE)
_URL_HOST_RE = re.compile(r"""(?:url|url-prefix)\(\s*["']?(?:https?://)?([^/"')\s]+)""", re.IGNORECASE)
_DEFAULTS = frozendict(
    colorsPath="~/.config/matugen/generated/dusky_sites.css",
    websitesDir="~/.config/dusky_sites",
    webThemeEnabled=False,
    forceUnthemedWebsites=False,
    userChromeEnabled=True,
    userContentEnabled=True,
)
_BOOL_KEYS = frozenset(key for key, value in _DEFAULTS.items() if isinstance(value, bool))


def _doc_hosts(content: str) -> set[str]:
    """Return the HTTP host keys the native host extracts from this template."""
    hosts = set()
    for specs in _DOCUMENT_RE.findall(content):
        hosts.update(h.lower() for h in (*_DOMAIN_RE.findall(specs), *_URL_HOST_RE.findall(specs)))
    return {h for h in hosts if h and ":" not in h}


def _disabled_sites(data: dict[str, Any]) -> set[str]:
    sites = data.get("disabledSites", [])
    if not isinstance(sites, list) or any(not isinstance(site, str) for site in sites):
        raise ValueError("disabledSites must be a JSON array of strings")
    return {site.strip().lower() for site in sites if site.strip()}


class DuskySitesEngine(BaseEngine):
    def __init__(self, config_path: str = "~/.config/dusky/settings/dusky_sites/config.json"):
        self.config_path = Path(config_path).expanduser().resolve()
        self.sites_dir = Path(_DEFAULTS["websitesDir"]).expanduser()
        self._lock = threading.Lock()
        self.cache: dict[str, Any] = {}

    @property
    def target_path(self) -> str:
        return str(self.config_path)

    def _read_config_json(self, *, strict: bool = False) -> dict[str, Any]:
        try:
            data = json.loads(self.config_path.read_text(encoding="utf-8"))
            if not isinstance(data, dict):
                raise ValueError("Target JSON must contain an object")
            _disabled_sites(data)
            for key in _BOOL_KEYS:
                if key in data and not isinstance(data[key], bool):
                    raise ValueError(f"{key} must be a JSON boolean")
            for key in ("colorsPath", "websitesDir"):
                path = data.get(key, _DEFAULTS[key])
                if not isinstance(path, str) or not path.strip() or "\0" in path or not Path(os.path.expanduser(path)).is_absolute():
                    raise ValueError(f"{key} must be an absolute path or begin with ~")
            return data
        except FileNotFoundError:
            return dict(_DEFAULTS) | {"disabledSites": []}
        except (OSError, ValueError):
            if strict:
                raise
            return dict(_DEFAULTS) | {"disabledSites": []}

    def _write_config_json(self, data: dict[str, Any]) -> None:
        self.config_path.parent.mkdir(parents=True, exist_ok=True)
        tmp_path = None
        try:
            with tempfile.NamedTemporaryFile("w", dir=self.config_path.parent, delete=False, encoding="utf-8") as tmp_file:
                tmp_path = Path(tmp_file.name)
                json.dump(data, tmp_file, indent=4, ensure_ascii=False)
                tmp_file.write("\n")
                tmp_file.flush()
                os.fsync(tmp_file.fileno())
            tmp_path.replace(self.config_path)
        finally:
            if tmp_path is not None:
                tmp_path.unlink(missing_ok=True)

    def get_site_files(self) -> list[Path]:
        # about.css belongs to profile stylesheets, not webpage injection.
        return sorted(path for path in self.sites_dir.glob("*.css") if path.is_file() and path.name != "about.css")

    def load_state(self) -> dict[str, Any]:
        with self._lock:
            data = self._read_config_json()
            self.sites_dir = Path(data.get("websitesDir", _DEFAULTS["websitesDir"])).expanduser()
            disabled = _disabled_sites(data)
            self.cache = {}
            for key, default in _DEFAULTS.items():
                value = data.get(key, default)
                self.cache[key] = value
                self.cache[f"DEFAULT/{key}"] = value
            for css_file in self.get_site_files():
                try:
                    hosts = _doc_hosts(css_file.read_text(encoding="utf-8"))
                except (OSError, UnicodeError):
                    hosts = set()
                enabled = not (hosts | {css_file.stem.lower()}) & disabled
                key = f"site_{css_file.stem.lower()}"
                self.cache[key] = enabled
                self.cache[f"DEFAULT/{key}"] = enabled
            return self.cache

    def write_value(self, target_key: str, target_scope: str, new_value: str, item_type: str = "string") -> tuple[bool, str, str]:
        return self.write_batch([(target_key, target_scope, new_value, item_type)])

    def write_batch(self, changes: list[tuple[str, str, str, str]]) -> tuple[bool, str, str]:
        if not changes:
            return True, "No changes pending.", ""
        with self._lock:
            try:
                self.config_path.parent.mkdir(parents=True, exist_ok=True)
                # Lock a stable sidecar: replacing config.json changes its inode.
                with self.config_path.with_name(self.config_path.name + ".lock").open("a", encoding="utf-8") as lock:
                    fcntl.flock(lock, fcntl.LOCK_EX)
                    data = self._read_config_json(strict=True)
                    self.sites_dir = Path(data.get("websitesDir", _DEFAULTS["websitesDir"])).expanduser()
                    disabled = _disabled_sites(data)
                    messages = []
                    for key, scope, value, item_type in changes:
                        if scope != "DEFAULT":
                            raise ValueError(f"Unsupported scope: {scope}")
                        text = str(value).strip().lower()
                        if text not in {"true", "1", "yes", "on", "t", "y", "false", "0", "no", "off", "f", "n"}:
                            raise ValueError(f"Invalid boolean for {key}: {value!r}")
                        enabled = text in {"true", "1", "yes", "on", "t", "y"}
                        if key in _BOOL_KEYS:
                            data[key] = enabled
                            messages.append(f"{key} set to {enabled}")
                        elif key.startswith("site_"):
                            stem = key.removeprefix("site_")
                            matches = [path for path in self.get_site_files() if path.stem.lower() == stem]
                            if not matches:
                                raise ValueError(f"Template no longer exists: {stem}")
                            hosts = {stem}
                            for path in matches:
                                hosts.update(_doc_hosts(path.read_text(encoding="utf-8")))
                            if enabled:
                                disabled.difference_update(hosts)
                            else:
                                disabled.update(hosts)
                            messages.append(f"{'Enabled' if enabled else 'Disabled'} theme for {stem}")
                        else:
                            raise ValueError(f"Unsupported setting: {key}")
                    data["disabledSites"] = sorted(disabled)
                    self._write_config_json(data)
            except (OSError, ValueError) as error:
                return False, f"Configuration was not saved: {error}", ""
        self.load_state()
        if any(key in {"userChromeEnabled", "userContentEnabled"} for key, *_ in changes):
            messages.append("Apply stylesheet settings with Install / Update, then restart Firefox")
        return True, "; ".join(messages), ""
