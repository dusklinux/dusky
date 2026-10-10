#!/usr/bin/env python3
"""
===============================================================================
DUSKY VIVID THEMES: DATA & PATH CONTRACTS
===============================================================================
Centralizes path resolution, preset schema validation, and discovery.
Python 3.15 standard library only.
===============================================================================
"""

from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Any, TypedDict

SOURCE_DIR = Path(__file__).resolve().parent
PRESETS_DIR = SOURCE_DIR / "presets"

raw_config = os.environ.get("XDG_CONFIG_HOME")
CONFIG_ROOT = Path(raw_config) if raw_config else Path.home() / ".config"
if not CONFIG_ROOT.is_absolute():
    raise ValueError(f"XDG_CONFIG_HOME must be an absolute path: {CONFIG_ROOT}")

MATUGEN_DIR = CONFIG_ROOT / "matugen"
CACHE_ROOT = MATUGEN_DIR / "prebaked"
CACHE_LOCK_PATH = MATUGEN_DIR / ".prebaked.lock"
PACKS_ROOT = CACHE_ROOT / "packs"
CATALOG_PATH = CACHE_ROOT / "catalog.json"
STAGING_ROOT = CACHE_ROOT / ".staging"

TEMPLATES_DIR = MATUGEN_DIR / "templates"
LIVE_CONFIG = MATUGEN_DIR / "config.toml"
GENERATED_DIR = MATUGEN_DIR / "generated"

STATE_DIR = CONFIG_ROOT / "dusky" / "settings" / "dusky_theme"
STATE_FILE = STATE_DIR / "state.conf"
RESUME_SNAPSHOT_FILE = STATE_DIR / "vivid_resume.json"
THEME_CTL = Path.home() / "user_scripts" / "theme_matugen" / "theme_ctl.sh"

_RE_ID = re.compile(r"^[a-z][a-z0-9_]*$")
_RE_HEX = re.compile(r"^#[0-9a-fA-F]{6}$")

VALID_SCHEMES = frozenset({
    "scheme-content",
    "scheme-expressive",
    "scheme-fidelity",
    "scheme-fruit-salad",
    "scheme-monochrome",
    "scheme-neutral",
    "scheme-rainbow",
    "scheme-tonal-spot",
    "scheme-vibrant",
    "scheme-smart",
})


class PresetDict(TypedDict, total=False):
    id: str
    label: str
    hex: str
    bg_hex: str
    mode: str
    scheme: str
    order: int
    desc: str


def _reject_duplicate_keys(ordered_pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    res: dict[str, Any] = {}
    for key, val in ordered_pairs:
        if key in res:
            raise ValueError(f"Duplicate JSON key: '{key}'")
        res[key] = val
    return res


ALLOWED_KEYS = frozenset({"id", "label", "hex", "bg_hex", "mode", "scheme", "order", "desc"})


def validate_preset(data: dict[str, Any], filepath: Path | None = None) -> PresetDict:
    """Validates and normalizes a single preset definition dictionary with strict type enforcement."""
    context = f" in {filepath.name}" if filepath else ""
    if not isinstance(data, dict):
        raise ValueError(f"Preset root must be a JSON object{context}")

    unknown = set(data.keys()) - ALLOWED_KEYS
    if unknown:
        raise ValueError(f"Unknown preset key(s) {unknown}{context}")

    raw_pid = data.get("id")
    if isinstance(raw_pid, bool) or not isinstance(raw_pid, str):
        raise ValueError(f"Preset missing or invalid 'id' type (must be str){context}")
    pid = raw_pid.strip().lower()
    if not pid or not _RE_ID.match(pid):
        raise ValueError(f"Invalid preset ID '{pid}'{context}. Must match [a-z][a-z0-9_]*")

    if filepath and filepath.stem != pid:
        raise ValueError(f"Preset file stem '{filepath.stem}' does not match preset id '{pid}'")

    raw_label = data.get("label")
    if isinstance(raw_label, bool) or not isinstance(raw_label, str):
        raise ValueError(f"Preset '{pid}' missing or invalid 'label' type (must be str){context}")
    label = raw_label.strip()
    if not label:
        raise ValueError(f"Preset '{pid}' missing non-empty 'label'{context}")

    raw_hex = data.get("hex")
    if isinstance(raw_hex, bool) or not isinstance(raw_hex, str):
        raise ValueError(f"Preset '{pid}' missing or invalid 'hex' type (must be str){context}")
    hex_code = raw_hex.strip().upper()
    if not _RE_HEX.match(hex_code):
        raise ValueError(f"Preset '{pid}' invalid 'hex' color '{hex_code}'{context}")

    raw_bg = data.get("bg_hex")
    if isinstance(raw_bg, bool) or not isinstance(raw_bg, str):
        raise ValueError(f"Preset '{pid}' missing or invalid 'bg_hex' type (must be str){context}")
    bg_hex = raw_bg.strip().lower()
    if not _RE_HEX.match(bg_hex):
        raise ValueError(f"Preset '{pid}' invalid 'bg_hex' color '{bg_hex}'{context}")

    raw_mode = data.get("mode", "dark")
    if isinstance(raw_mode, bool) or not isinstance(raw_mode, str):
        raise ValueError(f"Preset '{pid}' mode must be a string{context}")
    mode = raw_mode.strip().lower()
    if mode not in ("dark", "light"):
        raise ValueError(f"Preset '{pid}' mode must be 'dark' or 'light', got '{mode}'{context}")

    # Determine default scheme if omitted
    default_scheme = "scheme-monochrome" if hex_code in ("#FFFFFF", "#000000") else "scheme-fidelity"
    raw_scheme = data.get("scheme", default_scheme)
    if isinstance(raw_scheme, bool) or not isinstance(raw_scheme, str):
        raise ValueError(f"Preset '{pid}' scheme must be a string{context}")
    scheme = raw_scheme.strip().lower()
    if scheme not in VALID_SCHEMES:
        raise ValueError(f"Preset '{pid}' scheme '{scheme}' is not a recognized Matugen scheme{context}")

    raw_order = data.get("order", 10000)
    if isinstance(raw_order, bool) or not isinstance(raw_order, int):
        raise ValueError(f"Preset '{pid}' order must be an integer, got {type(raw_order).__name__}{context}")
    order = int(raw_order)

    raw_desc = data.get("desc")
    if isinstance(raw_desc, bool) or not isinstance(raw_desc, str):
        raise ValueError(f"Preset '{pid}' missing or invalid 'desc' type (must be str){context}")
    desc = raw_desc.strip()
    if not desc:
        raise ValueError(f"Preset '{pid}' missing non-empty 'desc'{context}")

    normalized: PresetDict = {
        "id": pid,
        "label": label,
        "hex": hex_code,
        "bg_hex": bg_hex,
        "mode": mode,
        "scheme": scheme,
        "order": order,
        "desc": desc,
    }
    return normalized


def load_presets_and_diagnostics() -> tuple[list[PresetDict], list[str]]:
    """
    Discovers, validates, and sorts all preset definitions.
    Returns (presets, diagnostics). Never resurrects legacy array.
    """
    presets: list[PresetDict] = []
    diagnostics: list[str] = []
    seen_ids: set[str] = set()

    if not PRESETS_DIR.is_dir():
        diagnostics.append(f"Presets directory missing: {PRESETS_DIR}")
        return presets, diagnostics

    for f in sorted(PRESETS_DIR.glob("*.json")):
        try:
            with open(f, "r", encoding="utf-8") as fp:
                raw = json.load(fp, object_pairs_hook=_reject_duplicate_keys)
            item = validate_preset(raw, filepath=f)
            if item["id"] in seen_ids:
                diagnostics.append(f"{f.name}: duplicate preset ID '{item['id']}'")
                continue
            seen_ids.add(item["id"])
            presets.append(item)
        except json.JSONDecodeError as e:
            diagnostics.append(f"{f.name}: JSON decode error at line {e.lineno}, col {e.colno}: {e.msg}")
        except Exception as e:
            diagnostics.append(f"{f.name}: {e}")

    presets.sort(key=lambda p: (p.get("order", 10000), p.get("id", "")))
    return presets, diagnostics


def load_presets() -> list[PresetDict]:
    """
    Discovers, validates, and sorts all preset definitions.
    Returns valid presets only.
    """
    presets, _ = load_presets_and_diagnostics()
    return presets


def load_preset_with_diagnostic(preset_id: str) -> tuple[PresetDict | None, str | None]:
    """Loads a specific preset by ID, returning (preset, diagnostic_if_error)."""
    pid = str(preset_id).strip().lower()
    if not _RE_ID.match(pid):
        return None, f"Invalid preset ID syntax: '{preset_id}'"
    file_path = PRESETS_DIR / f"{pid}.json"
    if not file_path.is_file():
        return None, f"Preset file '{pid}.json' not found in {PRESETS_DIR}"
    try:
        with open(file_path, "r", encoding="utf-8") as fp:
            raw = json.load(fp, object_pairs_hook=_reject_duplicate_keys)
        return validate_preset(raw, filepath=file_path), None
    except json.JSONDecodeError as e:
        return None, f"JSON decode error in {file_path.name} at line {e.lineno}, col {e.colno}: {e.msg}"
    except Exception as e:
        return None, f"Validation error in {file_path.name}: {e}"


def load_preset(preset_id: str) -> PresetDict | None:
    """Loads a specific preset by ID without scanning all other files."""
    p, _ = load_preset_with_diagnostic(preset_id)
    return p


def validate_all_definitions() -> list[str]:
    """
    Validates all definitions in PRESETS_DIR.
    Returns a list of error strings (empty if all definitions are valid).
    """
    if not PRESETS_DIR.is_dir():
        return [f"Presets directory not found: {PRESETS_DIR}"]

    json_files = list(PRESETS_DIR.glob("*.json"))
    if not json_files:
        return [f"No JSON presets found in {PRESETS_DIR}"]

    _, diagnostics = load_presets_and_diagnostics()
    return diagnostics


