#!/usr/bin/env python3
"""
===============================================================================
DUSKY TUI: HYBRID AUTOSTART ENGINE
===============================================================================
Bridges standard Hyprland Lua AST parsing for `hl.config({ ... })` tables,
while handling `hl.exec_cmd(...)` autostart directives inside `hl.on("hyprland.start", ...)`.
===============================================================================
"""

import os
import re
lazy import stat
lazy import tempfile
lazy from pathlib import Path
lazy from typing import Any

from python.engines.lua import HyprlandLuaEngine

AUTOSTART_DEFAULTS: dict[str, dict[str, Any]] = {
    # --- Interface & Background Services ---
    "autostart/awww_daemon": {
        "pattern": r'awww-daemon',
        "canonical": 'hl.exec_cmd("awww-daemon")',
        "default": True
    },
    "autostart/waybar": {
        "pattern": r'waybar_toggle\.sh|["\'\s]waybar(?:["\'\s]|$)',
        "canonical": 'hl.exec_cmd("$HOME/user_scripts/waybar/waybar_toggle.sh")',
        "default": True
    },
    "autostart/nm_applet": {
        "pattern": r'nm-applet',
        "canonical": 'hl.exec_cmd("nm-applet")',
        "default": False
    },
    "autostart/gnome_keyring": {
        "pattern": r'gnome-keyring-daemon',
        "canonical": 'hl.exec_cmd("/usr/bin/gnome-keyring-daemon --start --components=secrets")',
        "default": False
    },
    "autostart/xhost_root": {
        "pattern": r'xhost\s+\+si:localuser:root',
        "canonical": 'hl.exec_cmd("xhost +si:localuser:root")',
        "default": False
    },
    "autostart/hypridle": {
        "pattern": r'hypridle',
        "canonical": 'hl.exec_cmd("hypridle")',
        "default": False
    },
    "autostart/layout_notify": {
        "pattern": r'layout_notify\.sh',
        "canonical": 'hl.exec_cmd("$HOME/user_scripts/hypr/layout_notify.sh")',
        "default": False
    },
    "autostart/audio_visualizer": {
        "pattern": r'visualizer_toggle\.sh',
        "canonical": 'hl.exec_cmd("$HOME/user_scripts/way_layers/visualizer/visualizer_toggle.sh")',
        "default": False
    },
    "autostart/wayclick": {
        "pattern": r'dusky_wayclick\.sh',
        "canonical": 'hl.exec_cmd("$HOME/user_scripts/wayclick/dusky_wayclick.sh")',
        "default": False
    },
    "autostart/dusky_audio": {
        "pattern": r'dusky_audio_studio\.py.*--autostart',
        "canonical": 'hl.exec_cmd("python3 $HOME/user_scripts/audio/dusky_audio_studio/dusky_audio_studio.py --autostart")',
        "default": False
    },
    "autostart/localsend": {
        "pattern": r'hl\.exec_cmd\("localsend --hidden"\)|hl\.exec_cmd\("flatpak run org\.localsend',
        "canonical": 'hl.exec_cmd("localsend --hidden")',
        "default": False
    },
    "autostart/hyprpm_reload": {
        "pattern": r'hyprpm\s+reload',
        "canonical": 'hl.exec_cmd("hyprpm reload")',
        "default": False
    },

    # --- Clipboard Services ---
    "autostart/cliphist_text": {
        "pattern": r'^(?!.*cliphist_db_env).*wl-paste\s+--type\s+text\s+--watch\s+cliphist\s+store',
        "canonical": 'hl.exec_cmd("wl-paste --type text --watch cliphist store")',
        "default": False
    },
    "autostart/cliphist_image": {
        "pattern": r'^(?!.*cliphist_db_env).*wl-paste\s+--type\s+image\s+--watch\s+cliphist\s+store',
        "canonical": 'hl.exec_cmd("wl-paste --type image --watch cliphist store")',
        "default": False
    },
    "autostart/cliphist_db_text": {
        "pattern": r'cliphist_db_env.*text',
        "canonical": 'hl.exec_cmd("sh -c \'. $HOME/.config/dusky/settings/cliphist_db_env && exec wl-paste --type text --watch cliphist store\'")',
        "default": False
    },
    "autostart/cliphist_db_image": {
        "pattern": r'cliphist_db_env.*image',
        "canonical": 'hl.exec_cmd("sh -c \'. $HOME/.config/dusky/settings/cliphist_db_env && exec wl-paste --type image --watch cliphist store\'")',
        "default": False
    },
    "autostart/clip_persist": {
        "pattern": r'wl-clip-persist',
        "canonical": 'hl.exec_cmd("wl-clip-persist --clipboard regular")',
        "default": False
    },

    # --- Environment Integration ---
    "autostart/systemd_env": {
        "pattern": r'systemctl\s+--user\s+import-environment',
        "canonical": 'hl.exec_cmd("systemctl --user import-environment WAYLAND_DISPLAY XDG_CURRENT_DESKTOP XDG_SESSION_TYPE XDG_SESSION_DESKTOP CLIPHIST_DB_PATH")',
        "default": True
    },
    "autostart/dbus_env": {
        "pattern": r'dbus-update-activation-environment',
        "canonical": 'hl.exec_cmd("dbus-update-activation-environment --systemd --all")',
        "default": True
    },
    "autostart/session_target": {
        "pattern": r'systemctl\s+--user\s+start\s+hyprland-session\.target',
        "canonical": 'hl.exec_cmd("systemctl --user start hyprland-session.target")',
        "default": True
    },
    "autostart/shutdown_target": {
        "pattern": r'systemctl\s+--user\s+stop\s+hyprland-session\.target',
        "canonical": 'hl.exec_cmd("systemctl --user stop hyprland-session.target")',
        "default": True
    },
    "autostart/choom_oom": {
        "pattern": r'choom\s+-n\s+-250',
        "canonical": 'hl.exec_cmd("sudo choom -n -250 -p $(pgrep -x Hyprland)")',
        "default": True
    },

    # --- Dusky Glance Dashboards Autostart ---
    "autostart/glance_cpu": {
        "pattern": r'dusky_glance\.sh\s+--cpu(?:["\'\s]|$)',
        "canonical": 'hl.exec_cmd("~/user_scripts/rofi/dusky_glance.sh --cpu")',
        "default": False
    },
    "autostart/glance_cpu_power": {
        "pattern": r'dusky_glance\.sh\s+--cpu-power',
        "canonical": 'hl.exec_cmd("~/user_scripts/rofi/dusky_glance.sh --cpu-power")',
        "default": False
    },
    "autostart/glance_ram": {
        "pattern": r'dusky_glance\.sh\s+--ram(?:["\'\s]|$)',
        "canonical": 'hl.exec_cmd("~/user_scripts/rofi/dusky_glance.sh --ram")',
        "default": False
    },
    "autostart/glance_ram_temp": {
        "pattern": r'dusky_glance\.sh\s+--ram-temp',
        "canonical": 'hl.exec_cmd("~/user_scripts/rofi/dusky_glance.sh --ram-temp")',
        "default": False
    },
    "autostart/glance_zram": {
        "pattern": r'dusky_glance\.sh\s+--zram',
        "canonical": 'hl.exec_cmd("~/user_scripts/rofi/dusky_glance.sh --zram")',
        "default": False
    },
    "autostart/glance_temp": {
        "pattern": r'dusky_glance\.sh\s+--temp(?:["\'\s]|$)',
        "canonical": 'hl.exec_cmd("~/user_scripts/rofi/dusky_glance.sh --temp")',
        "default": False
    },
    "autostart/glance_battery": {
        "pattern": r'dusky_glance\.sh\s+--battery(?:["\'\s]|$)',
        "canonical": 'hl.exec_cmd("~/user_scripts/rofi/dusky_glance.sh --battery")',
        "default": False
    },
    "autostart/glance_battery_percent": {
        "pattern": r'dusky_glance\.sh\s+--battery-percent',
        "canonical": 'hl.exec_cmd("~/user_scripts/rofi/dusky_glance.sh --battery-percent")',
        "default": False
    },
    "autostart/glance_battery_watts": {
        "pattern": r'dusky_glance\.sh\s+--battery-watts',
        "canonical": 'hl.exec_cmd("~/user_scripts/rofi/dusky_glance.sh --battery-watts")',
        "default": False
    },
    "autostart/glance_battery_time": {
        "pattern": r'dusky_glance\.sh\s+--battery-time',
        "canonical": 'hl.exec_cmd("~/user_scripts/rofi/dusky_glance.sh --battery-time")',
        "default": False
    },
    "autostart/glance_gpu_power": {
        "pattern": r'dusky_glance\.sh\s+--gpu-power',
        "canonical": 'hl.exec_cmd("~/user_scripts/rofi/dusky_glance.sh --gpu-power card1 Intel")',
        "default": False
    },
    "autostart/glance_gpu_usage": {
        "pattern": r'dusky_glance\.sh\s+--gpu-usage',
        "canonical": 'hl.exec_cmd("~/user_scripts/rofi/dusky_glance.sh --gpu-usage card1 Intel")',
        "default": False
    },
    "autostart/glance_gpu_mem": {
        "pattern": r'dusky_glance\.sh\s+--gpu-mem',
        "canonical": 'hl.exec_cmd("~/user_scripts/rofi/dusky_glance.sh --gpu-mem card1 Intel")',
        "default": False
    },
    "autostart/glance_network": {
        "pattern": r'dusky_glance\.sh\s+--network(?:["\'\s]|$)',
        "canonical": 'hl.exec_cmd("~/user_scripts/rofi/dusky_glance.sh --network")',
        "default": False
    },
    "autostart/glance_network_down": {
        "pattern": r'dusky_glance\.sh\s+--network-down(?:["\'\s]|$)',
        "canonical": 'hl.exec_cmd("~/user_scripts/rofi/dusky_glance.sh --network-down")',
        "default": False
    },
    "autostart/glance_network_up": {
        "pattern": r'dusky_glance\.sh\s+--network-up(?:["\'\s]|$)',
        "canonical": 'hl.exec_cmd("~/user_scripts/rofi/dusky_glance.sh --network-up")',
        "default": False
    },
    "autostart/glance_network_combined": {
        "pattern": r'dusky_glance\.sh\s+--network-combined(?:["\'\s]|$)',
        "canonical": 'hl.exec_cmd("~/user_scripts/rofi/dusky_glance.sh --network-combined")',
        "default": False
    },
    "autostart/glance_network_down_session": {
        "pattern": r'dusky_glance\.sh\s+--network-down-session(?:["\'\s]|$)',
        "canonical": 'hl.exec_cmd("~/user_scripts/rofi/dusky_glance.sh --network-down-session")',
        "default": False
    },
    "autostart/glance_network_up_session": {
        "pattern": r'dusky_glance\.sh\s+--network-up-session(?:["\'\s]|$)',
        "canonical": 'hl.exec_cmd("~/user_scripts/rofi/dusky_glance.sh --network-up-session")',
        "default": False
    },
    "autostart/glance_network_session": {
        "pattern": r'dusky_glance\.sh\s+--network-session(?:["\'\s]|$)',
        "canonical": 'hl.exec_cmd("~/user_scripts/rofi/dusky_glance.sh --network-session")',
        "default": False
    },
    "autostart/glance_network_boot_down": {
        "pattern": r'dusky_glance\.sh\s+--network-boot-down(?:["\'\s]|$)',
        "canonical": 'hl.exec_cmd("~/user_scripts/rofi/dusky_glance.sh --network-boot-down")',
        "default": False
    },
    "autostart/glance_network_boot_up": {
        "pattern": r'dusky_glance\.sh\s+--network-boot-up(?:["\'\s]|$)',
        "canonical": 'hl.exec_cmd("~/user_scripts/rofi/dusky_glance.sh --network-boot-up")',
        "default": False
    },
    "autostart/glance_network_boot": {
        "pattern": r'dusky_glance\.sh\s+--network-boot(?:["\'\s]|$)',
        "canonical": 'hl.exec_cmd("~/user_scripts/rofi/dusky_glance.sh --network-boot")',
        "default": False
    },
    "autostart/glance_uptime": {
        "pattern": r'dusky_glance\.sh\s+--uptime',
        "canonical": 'hl.exec_cmd("~/user_scripts/rofi/dusky_glance.sh --uptime")',
        "default": False
    },
    "autostart/glance_workspace": {
        "pattern": r'dusky_glance\.sh\s+--workspace',
        "canonical": 'hl.exec_cmd("~/user_scripts/rofi/dusky_glance.sh --workspace")',
        "default": False
    },
    "autostart/glance_clock": {
        "pattern": r'dusky_glance\.sh\s+--clock(?:["\'\s]|$)',
        "canonical": 'hl.exec_cmd("~/user_scripts/rofi/dusky_glance.sh --clock")',
        "default": False
    },
    "autostart/glance_clock_short": {
        "pattern": r'dusky_glance\.sh\s+--clock-short',
        "canonical": 'hl.exec_cmd("~/user_scripts/rofi/dusky_glance.sh --clock-short")',
        "default": False
    },
    "autostart/glance_disk": {
        "pattern": r'dusky_glance\.sh\s+--disk(?:["\'\s]|$)',
        "canonical": 'hl.exec_cmd("~/user_scripts/rofi/dusky_glance.sh --disk")',
        "default": False
    },
    "autostart/glance_disk_read": {
        "pattern": r'dusky_glance\.sh\s+--disk-read',
        "canonical": 'hl.exec_cmd("~/user_scripts/rofi/dusky_glance.sh --disk-read nvme0n1")',
        "default": False
    },
    "autostart/glance_disk_write": {
        "pattern": r'dusky_glance\.sh\s+--disk-write',
        "canonical": 'hl.exec_cmd("~/user_scripts/rofi/dusky_glance.sh --disk-write nvme0n1")',
        "default": False
    },
    "autostart/glance_disk_temp": {
        "pattern": r'dusky_glance\.sh\s+--disk-temp',
        "canonical": 'hl.exec_cmd("~/user_scripts/rofi/dusky_glance.sh --disk-temp nvme0n1")',
        "default": False
    },
    "autostart/glance_stopwatch": {
        "pattern": r'dusky_glance\.sh\s+--stopwatch',
        "canonical": 'hl.exec_cmd("~/user_scripts/rofi/dusky_glance.sh --stopwatch")',
        "default": False
    },
    "autostart/glance_timer": {
        "pattern": r'dusky_glance\.sh\s+--timer',
        "canonical": 'hl.exec_cmd("~/user_scripts/rofi/dusky_glance.sh --timer 15m")',
        "default": False
    },
    "autostart/glance_hud": {
        "pattern": r'dusky_glance\.sh\s+--hud',
        "canonical": 'hl.exec_cmd("~/user_scripts/rofi/dusky_glance.sh --hud card1 Intel")',
        "default": False
    },
    "autostart/glance_world_ny": {
        "pattern": r'dusky_glance\.sh\s+--world-clock\s+America/New_York',
        "canonical": 'hl.exec_cmd("~/user_scripts/rofi/dusky_glance.sh --world-clock America/New_York NY")',
        "default": False
    },
    "autostart/glance_world_tokyo": {
        "pattern": r'dusky_glance\.sh\s+--world-clock\s+Asia/Tokyo',
        "canonical": 'hl.exec_cmd("~/user_scripts/rofi/dusky_glance.sh --world-clock Asia/Tokyo Japan")',
        "default": False
    },
    "autostart/glance_world_london": {
        "pattern": r'dusky_glance\.sh\s+--world-clock\s+Europe/London',
        "canonical": 'hl.exec_cmd("~/user_scripts/rofi/dusky_glance.sh --world-clock Europe/London London")',
        "default": False
    },
}

def _is_header_comment(lines: list[str], idx: int) -> bool:
    """Returns True if the line is part of the top-level doc/syntax header comments."""
    if idx < 15 and idx < len(lines):
        # Only comment lines can be header; active code in the first 15
        # lines must still be honoured (short/test files).
        if not lines[idx].strip().startswith("--"):
            return False
        for i in range(min(idx + 1, 15)):
            if "Syntax:" in lines[i] or "USER CONFIGURATION" in lines[i]:
                return True
    return False

class AutostartLuaEngine(HyprlandLuaEngine):
    def load_state(self) -> dict[str, Any]:
        state = super().load_state()

        if not self.config_path.exists():
            for full_key, meta in AUTOSTART_DEFAULTS.items():
                state[full_key] = meta["default"]
            return state

        try:
            content = self.config_path.read_text(encoding="utf-8")
        except OSError:
            for full_key, meta in AUTOSTART_DEFAULTS.items():
                state[full_key] = meta["default"]
            return state

        lines = content.splitlines()

        for full_key, meta in AUTOSTART_DEFAULTS.items():
            pattern = meta["pattern"]
            is_active = False
            found_commented = False

            for idx, line in enumerate(lines):
                if _is_header_comment(lines, idx):
                    continue

                stripped = line.strip()
                if re.search(pattern, stripped):
                    if stripped.startswith("--"):
                        found_commented = True
                    else:
                        is_active = True
                        break

            if is_active:
                state[full_key] = True
            elif found_commented:
                state[full_key] = False
            else:
                state[full_key] = meta["default"]

        return state

    def write_batch(self, changes: list[tuple[str, str, str, str]]) -> tuple[bool, str, str]:
        standard_changes = []
        autostart_changes = []

        for key, scope, val, itype in changes:
            full_key = f"{scope}/{key}" if scope and scope != "DEFAULT" else f"autostart/{key}"
            if scope == "autostart" or full_key in AUTOSTART_DEFAULTS or f"autostart/{key}" in AUTOSTART_DEFAULTS:
                autostart_changes.append((key, scope, val, itype))
            else:
                standard_changes.append((key, scope, val, itype))

        success = True
        msg = ""
        debug = ""

        if standard_changes:
            success, msg, debug = super().write_batch(standard_changes)

        if not success:
            return success, msg, debug

        if autostart_changes:
            try:
                content = self.config_path.read_text(encoding="utf-8") if self.config_path.exists() else ""
                lines = content.splitlines() if content else []

                has_start_block = any("hyprland.start" in l for l in lines)

                for key, scope, val_str, _ in autostart_changes:
                    full_key = f"{scope}/{key}" if scope and scope != "DEFAULT" else f"autostart/{key}"
                    meta = AUTOSTART_DEFAULTS.get(full_key) or AUTOSTART_DEFAULTS.get(f"autostart/{key}")

                    if not meta:
                        continue

                    pattern = meta["pattern"]
                    canonical = meta["canonical"]
                    want_enabled = str(val_str).lower() in ("true", "1", "yes", "on", "t", "y")

                    matched_idx = -1
                    is_currently_commented = False

                    for idx, line in enumerate(lines):
                        if _is_header_comment(lines, idx):
                            continue

                        stripped = line.strip()
                        if re.search(pattern, stripped):
                            matched_idx = idx
                            is_currently_commented = stripped.startswith("--")
                            if not is_currently_commented:
                                break

                    if want_enabled:
                        if matched_idx != -1:
                            if is_currently_commented:
                                raw_line = lines[matched_idx]
                                indent = raw_line[:len(raw_line) - len(raw_line.lstrip())]
                                uncommented = re.sub(r'^\s*--+\s*', '', raw_line)
                                lines[matched_idx] = indent + uncommented
                        else:
                            insert_line = f"    {canonical}"
                            block_end_idx = -1
                            block_start_idx = -1

                            for i, l in enumerate(lines):
                                if "hyprland.start" in l and not _is_header_comment(lines, i):
                                    block_start_idx = i
                                elif block_start_idx != -1 and l.strip().startswith("end)"):
                                    block_end_idx = i
                                    break

                            if block_end_idx != -1:
                                lines.insert(block_end_idx, insert_line)
                            else:
                                if not has_start_block:
                                    lines.append('\nhl.on("hyprland.start", function()')
                                    lines.append(insert_line)
                                    lines.append('end)\n')
                                    has_start_block = True
                                else:
                                    lines.append(insert_line)

                    else: # want_enabled is False
                        if matched_idx != -1 and not is_currently_commented:
                            raw_line = lines[matched_idx]
                            indent = raw_line[:len(raw_line) - len(raw_line.lstrip())]
                            lines[matched_idx] = f"{indent}-- {raw_line.strip()}"

                new_content = "\n".join(lines) + "\n"
                self.config_path.parent.mkdir(parents=True, exist_ok=True)

                out_fd, tmp_path_str = tempfile.mkstemp(dir=self.config_path.parent, text=True, suffix=".lua")
                with os.fdopen(out_fd, "w", encoding="utf-8") as f:
                    f.write(new_content)

                tmp_path = Path(tmp_path_str)
                if self.config_path.exists():
                    try:
                        tmp_path.chmod(stat.S_IMODE(self.config_path.stat().st_mode))
                    except OSError:
                        pass

                tmp_path.replace(self.config_path)

                if hasattr(self, "file_mtimes"):
                    self.file_mtimes[str(self.config_path)] = self.config_path.stat().st_mtime

                msg = f"Successfully batched {len(changes)} autostart changes."

            except Exception as e:
                return False, f"Autostart Engine failed to write changes: {e}", debug

        return True, msg, debug
