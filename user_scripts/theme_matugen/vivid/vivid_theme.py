#!/usr/bin/env python3
"""
===============================================================================
DUSKY VIVID THEME ENGINE (SHARED CORE v2.1)
===============================================================================
Target: Arch Linux / Hyprland / Matugen / Python 3.15
Provides warm, instant switching for pre-baked vivid color packs.
Coordinates file publication, GTK3/4 GSettings flips, Qt/KDE notifications,
and desktop process reloads without invoking Matugen during healthy applies.
Integrates JIT self-healing via vivid_bake.ensure_pack() on cache misses,
direct in-memory payload deployment, and checked publication statuses.
===============================================================================
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Any

# Ensure sibling and global paths
_VIVID_DIR = Path(__file__).resolve().parent
_GLOBAL_DIR = _VIVID_DIR.parent / "global"
if str(_GLOBAL_DIR) not in sys.path:
    sys.path.insert(0, str(_GLOBAL_DIR))
if str(_VIVID_DIR) not in sys.path:
    sys.path.insert(0, str(_VIVID_DIR))

import vivid_data
from vivid_data import (
    CONFIG_ROOT,
    GENERATED_DIR,
    RESUME_SNAPSHOT_FILE,
    STATE_DIR,
    STATE_FILE,
    THEME_CTL,
    PresetDict,
)

# Python 3.15 lazy imports: avoid heavy graphics/toolkit imports on list/status
lazy import vivid_bake
from theme_files import atomic_write, publication_lock
lazy import gtk_colors
lazy import qt_colors
lazy import kde_colors

type VividResult = tuple[bool, str, str]


def list_vivid_presets() -> list[PresetDict]:
    """Returns the ordered, validated list of all preset source definitions."""
    return vivid_data.load_presets()


def get_active_vivid_info() -> tuple[str, str, bool]:
    """Returns (preset_id, hex_code, is_vivid) by inspecting state.conf."""
    if not STATE_FILE.is_file():
        return "none", "#00FF00", False

    state = _read_state_conf()
    source = state.get("LAST_THEME_SOURCE", "").strip('"\'')
    preset_id = state.get("LAST_VIVID_PRESET", "").strip('"\'')
    hex_code = state.get("LAST_APPLIED_HEX", "#00FF00").strip('"\'')

    if source == "vivid" and preset_id and preset_id != "none":
        return preset_id, hex_code, True
    return "none", hex_code, False


def _read_state_conf() -> dict[str, str]:
    state: dict[str, str] = {}
    if not STATE_FILE.is_file():
        return state
    for line in STATE_FILE.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if "=" in line:
            k, v = line.split("=", 1)
            state[k.strip()] = v.strip()
    return state


def _write_state_conf_updates(updates: dict[str, str]) -> None:
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    lines: list[str] = []
    found_keys: set[str] = set()

    if STATE_FILE.is_file():
        for line in STATE_FILE.read_text(encoding="utf-8").splitlines():
            stripped = line.strip()
            if not stripped or stripped.startswith("#"):
                lines.append(line)
                continue
            if "=" in stripped:
                k, _ = stripped.split("=", 1)
                k = k.strip()
                if k in updates:
                    val = updates[k]
                    lines.append(f'{k}="{val}"' if not val.startswith('"') and " " in val else f"{k}={val}")
                    found_keys.add(k)
                else:
                    lines.append(line)
            else:
                lines.append(line)

    for k, val in updates.items():
        if k not in found_keys:
            lines.append(f'{k}="{val}"' if not val.startswith('"') and " " in val else f"{k}={val}")

    atomic_write(STATE_FILE, "\n".join(lines) + "\n")


def _atomic_write_bytes(target: Path, data: bytes) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    fd, temp_path = tempfile.mkstemp(prefix=f".{target.name}.", dir=target.parent)
    try:
        with os.fdopen(fd, "wb") as dst:
            dst.write(data)
            os.fchmod(dst.fileno(), 0o644)
        os.replace(temp_path, target)
    finally:
        if os.path.exists(temp_path):
            os.unlink(temp_path)


def _ensure_resume_snapshot(current_state: dict[str, str]) -> None:
    """Snapshots pre-vivid theme state if not already in vivid mode."""
    if not RESUME_SNAPSHOT_FILE.exists():
        snapshot = {
            "THEME_MODE": current_state.get("THEME_MODE", "dark").strip('"\''),
            "LAST_APPLIED_HEX": current_state.get("LAST_APPLIED_HEX", "").strip('"\''),
            "MATUGEN_TYPE": current_state.get("MATUGEN_TYPE", "").strip('"\''),
            "MATUGEN_CONTRAST": current_state.get("MATUGEN_CONTRAST", "").strip('"\''),
        }
        atomic_write(RESUME_SNAPSHOT_FILE, json.dumps(snapshot, indent=2))


def _dispatch_desktop_reloads() -> list[str]:
    """Dispatches required signals to running desktop consumers with bounded timeouts."""
    warnings: list[str] = []
    # 1. Hyprland
    try:
        res = subprocess.run(["hyprctl", "reload", "config-only"], capture_output=True, timeout=5)
        if res.returncode != 0:
            warnings.append(f"hyprctl ({res.returncode})")
    except Exception as e:
        warnings.append(f"hyprctl: {e}")

    # 2. Waybar (pkill returns 1 if not running, which is normal)
    try:
        res = subprocess.run(["pkill", "-USR2", "-x", "waybar"], capture_output=True, timeout=5)
        if res.returncode > 1:
            warnings.append(f"waybar ({res.returncode})")
    except Exception as e:
        warnings.append(f"waybar: {e}")

    # 3. Kitty
    try:
        res = subprocess.run(["pkill", "-USR1", "-x", "kitty"], capture_output=True, timeout=5)
        if res.returncode > 1:
            warnings.append(f"kitty ({res.returncode})")
    except Exception as e:
        warnings.append(f"kitty: {e}")

    # 4. Mako
    try:
        res = subprocess.run(["makoctl", "reload"], capture_output=True, timeout=5)
        if res.returncode != 0:
            warnings.append(f"makoctl ({res.returncode})")
    except Exception as e:
        warnings.append(f"makoctl: {e}")

    # 5. Neovim
    try:
        res = subprocess.run(["pkill", "-USR1", "-x", "nvim"], capture_output=True, timeout=5)
        if res.returncode > 1:
            warnings.append(f"nvim ({res.returncode})")
    except Exception as e:
        warnings.append(f"nvim: {e}")

    # 6. Cava
    try:
        res = subprocess.run(["pkill", "-USR2", "-x", "cava"], capture_output=True, timeout=5)
        if res.returncode > 1:
            warnings.append(f"cava ({res.returncode})")
    except Exception as e:
        warnings.append(f"cava: {e}")

    return warnings


def apply_vivid(preset_id: str) -> VividResult:
    """
    Applies a pre-baked vivid preset:
    1. Ensures pack is fresh and verified via vivid_bake.ensure_pack() (self-healing JIT).
    2. Atomically publishes template outputs directly from verified payload bytes in memory.
    3. Runs GTK, Qt, and KDE publishers (Qt publisher owns update & reload notifications).
    4. Dispatches desktop process reloads.
    5. Records state metadata.
    """
    start_time = time.perf_counter_ns()

    # 1. Ensure verified pack exists (outside desktop publication lock)
    # Returns canonical PreparedPack with synchronized metadata and payloads
    ok, prepared, ensure_msg = vivid_bake.ensure_pack(preset_id)
    if not ok or not prepared or not prepared.payloads:
        return False, f"Failed to ensure pack: {ensure_msg}", ""

    # 2. Acquire desktop publication lock
    with publication_lock("vivid"):
        current_state = _read_state_conf()
        current_mode = current_state.get("THEME_MODE", "dark").strip('"\'')
        target_mode = prepared.mode

        # Reconcile mode if needed
        if current_mode != target_mode:
            if not THEME_CTL.is_file() or not os.access(THEME_CTL, os.X_OK):
                return False, f"Theme controller not available at {THEME_CTL} for required mode switch to {target_mode}", prepared.hex

            _ensure_resume_snapshot(current_state)
            proc = subprocess.run(
                [str(THEME_CTL), "set", "--mode", target_mode, "--no-wall", "--no-regen"],
                capture_output=True,
                text=True,
                timeout=10,
            )
            if proc.returncode != 0:
                return False, f"Mode switch to {target_mode} failed: {proc.stderr.strip()}", prepared.hex
        else:
            _ensure_resume_snapshot(current_state)

        files_written = False
        try:
            # Pre-check destination directories exist and writable
            GENERATED_DIR.mkdir(parents=True, exist_ok=True)
            (CONFIG_ROOT / "gtk-4.0").mkdir(parents=True, exist_ok=True)

            # Atomically write generated template outputs directly from verified payload bytes
            for rel_path, data in prepared.payloads.items():
                if rel_path.startswith("generated/"):
                    target_file = GENERATED_DIR / Path(rel_path).name
                    _atomic_write_bytes(target_file, data)

            gtk4_bytes = prepared.payloads.get("generated/gtk-4.css")
            if gtk4_bytes:
                _atomic_write_bytes(CONFIG_ROOT / "gtk-4.0/gtk.css", gtk4_bytes)

            files_written = True

            # Execute toolkit publishers (Qt publisher owns normalization and notification!)
            gtk_colors.publish("3")
            qt_colors.publish("qt5ct")
            qt_colors.publish("qt6ct")
            qt_colors.publish("kvantum_kvconfig")
            qt_colors.publish("kvantum_svg")
            kde_colors.publish()

            # Desktop signals
            dispatch_warnings = _dispatch_desktop_reloads()
            publication_status = "complete" if not dispatch_warnings else "partial_reload_warning"

            # Update state.conf using prepared canonical ID, mode, and accent
            state_updates = {
                "THEME_MODE": f'"{target_mode}"',
                "LAST_APPLIED_HEX": prepared.hex,
                "LAST_THEME_SOURCE": '"vivid"',
                "LAST_VIVID_PRESET": f'"{prepared.id}"',
                "LAST_VIVID_BUILD": f'"{prepared.fingerprint}"',
                "LAST_VIVID_MODE": f'"{target_mode}"',
                "LAST_VIVID_PUBLICATION": f'"{publication_status}"',
            }
            _write_state_conf_updates(state_updates)

            elapsed_ms = (time.perf_counter_ns() - start_time) / 1_000_000
            msg = f"Applied vivid preset '{prepared.id}' ({prepared.hex}) in {elapsed_ms:.1f}ms"
            if "unavailable" in prepared.message:
                msg += f" [{prepared.message}]"
            if dispatch_warnings:
                msg += f" (dispatch warnings: {', '.join(dispatch_warnings)})"

            return True, msg, prepared.hex

        except Exception as e:
            if files_written:
                _write_state_conf_updates({"LAST_VIVID_PUBLICATION": '"failed"'})
                return False, f"Partial publication failure after files were written: {e}", prepared.hex
            return False, f"Publication preflight failed: {e}", prepared.hex


def resume_wallpaper() -> VividResult:
    """Restores desktop back to dynamic wallpaper-derived theming."""
    if not THEME_CTL.is_file() or not os.access(THEME_CTL, os.X_OK):
        return False, f"Theme controller not executable at: {THEME_CTL}", ""

    with publication_lock("vivid"):
        prev_mode = "dark"
        if RESUME_SNAPSHOT_FILE.is_file():
            try:
                raw_snap = RESUME_SNAPSHOT_FILE.read_text(encoding="utf-8")
                snap = json.loads(raw_snap)
                if isinstance(snap, dict):
                    m = snap.get("THEME_MODE", "dark")
                    if m in ("dark", "light"):
                        prev_mode = m
            except Exception:
                pass

        # Reconcile mode with snapshot mode if changed
        curr_mode = _read_state_conf().get("THEME_MODE", "dark").strip('"\'')
        if curr_mode != prev_mode:
            proc = subprocess.run(
                [str(THEME_CTL), "set", "--mode", prev_mode, "--no-wall", "--no-regen"],
                capture_output=True,
                text=True,
                timeout=10,
            )
            if proc.returncode != 0:
                return False, f"Failed to restore mode to {prev_mode}: {proc.stderr.strip()}", ""

        # Execute refresh to regenerate from wallpaper
        proc = subprocess.run(
            [str(THEME_CTL), "refresh"],
            capture_output=True,
            text=True,
            timeout=30,
        )
        if proc.returncode != 0:
            err = proc.stderr.strip() or "theme_ctl refresh failed"
            return False, f"Failed to refresh wallpaper theme: {err}", ""

        # Update state.conf to reflect return to wallpaper before unlinking snapshot (F07)
        _write_state_conf_updates({
            "LAST_THEME_SOURCE": '"wallpaper"',
            "LAST_VIVID_PRESET": '"none"',
            "LAST_VIVID_BUILD": '"none"',
            "LAST_VIVID_MODE": '"none"',
            "LAST_VIVID_PUBLICATION": '"none"',
        })

        # Remove snapshot ONLY after successful state commit (F07)
        RESUME_SNAPSHOT_FILE.unlink(missing_ok=True)

        new_state = _read_state_conf()
        resumed_hex = new_state.get("LAST_APPLIED_HEX", "#00FF00").strip('"\'')
        return True, "Resumed dynamic wallpaper theming", resumed_hex
