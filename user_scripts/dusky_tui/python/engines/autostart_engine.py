#!/usr/bin/env python3
"""
===============================================================================
DUSKY TUI: HYBRID AUTOSTART ENGINE
===============================================================================
Bridges standard Hyprland Lua AST parsing for `hl.config({ ... })` tables,
while handling `hl.exec_cmd(...)` autostart directives inside `hl.on("hyprland.start", ...)`.
===============================================================================
"""

import re
lazy import shlex
lazy from bisect import bisect_right
lazy from pathlib import Path
lazy from typing import Any

from python.engines.lua import HyprlandLuaEngine
from python.shared.lua_source import calls, tokens, string_value, line_comments

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
        "pattern": r"""dusky_glance\.sh[\\"']*\s+--cpu(?:["\'\s]|$)""",
        "canonical": 'hl.exec_cmd("~/user_scripts/rofi/dusky_glance.sh --cpu")',
        "default": False
    },
    "autostart/glance_cpu_power": {
        "pattern": r"""dusky_glance\.sh[\\"']*\s+--cpu-power""",
        "canonical": 'hl.exec_cmd("~/user_scripts/rofi/dusky_glance.sh --cpu-power")',
        "default": False
    },
    "autostart/glance_ram": {
        "pattern": r"""dusky_glance\.sh[\\"']*\s+--ram(?:["\'\s]|$)""",
        "canonical": 'hl.exec_cmd("~/user_scripts/rofi/dusky_glance.sh --ram")',
        "default": False
    },
    "autostart/glance_ram_available": {
        "pattern": r"""dusky_glance\.sh[\\"']*\s+--ram-available(?:["\'\s]|$)""",
        "canonical": 'hl.exec_cmd("~/user_scripts/rofi/dusky_glance.sh --ram-available")',
        "default": False
    },
    "autostart/glance_ram_free": {
        "pattern": r"""dusky_glance\.sh[\\"']*\s+--ram-free(?:["\'\s]|$)""",
        "canonical": 'hl.exec_cmd("~/user_scripts/rofi/dusky_glance.sh --ram-free")',
        "default": False
    },
    "autostart/glance_ram_temp": {
        "pattern": r"""dusky_glance\.sh[\\"']*\s+--ram-temp""",
        "canonical": 'hl.exec_cmd("~/user_scripts/rofi/dusky_glance.sh --ram-temp")',
        "default": False
    },
    "autostart/glance_zram": {
        "pattern": r"""dusky_glance\.sh[\\"']*\s+--zram""",
        "canonical": 'hl.exec_cmd("~/user_scripts/rofi/dusky_glance.sh --zram")',
        "default": False
    },
    "autostart/glance_temp": {
        "pattern": r"""dusky_glance\.sh[\\"']*\s+--temp(?:["\'\s]|$)""",
        "canonical": 'hl.exec_cmd("~/user_scripts/rofi/dusky_glance.sh --temp")',
        "default": False
    },
    "autostart/glance_battery": {
        "pattern": r"""dusky_glance\.sh[\\"']*\s+--battery(?:["\'\s]|$)""",
        "canonical": 'hl.exec_cmd("~/user_scripts/rofi/dusky_glance.sh --battery")',
        "default": False
    },
    "autostart/glance_battery_percent": {
        "pattern": r"""dusky_glance\.sh[\\"']*\s+--battery-percent""",
        "canonical": 'hl.exec_cmd("~/user_scripts/rofi/dusky_glance.sh --battery-percent")',
        "default": False
    },
    "autostart/glance_battery_watts": {
        "pattern": r"""dusky_glance\.sh[\\"']*\s+--battery-watts""",
        "canonical": 'hl.exec_cmd("~/user_scripts/rofi/dusky_glance.sh --battery-watts")',
        "default": False
    },
    "autostart/glance_battery_time": {
        "pattern": r"""dusky_glance\.sh[\\"']*\s+--battery-time""",
        "canonical": 'hl.exec_cmd("~/user_scripts/rofi/dusky_glance.sh --battery-time")',
        "default": False
    },
    "autostart/glance_gpu_power": {
        "pattern": r"""dusky_glance\.sh[\\"']*\s+--gpu-power""",
        "canonical": 'hl.exec_cmd("~/user_scripts/rofi/dusky_glance.sh --gpu-power")',
        "default": False
    },
    "autostart/glance_gpu_usage": {
        "pattern": r"""dusky_glance\.sh[\\"']*\s+--gpu-usage""",
        "canonical": 'hl.exec_cmd("~/user_scripts/rofi/dusky_glance.sh --gpu-usage")',
        "default": False
    },
    "autostart/glance_gpu_mem": {
        "pattern": r"""dusky_glance\.sh[\\"']*\s+--gpu-mem""",
        "canonical": 'hl.exec_cmd("~/user_scripts/rofi/dusky_glance.sh --gpu-mem")',
        "default": False
    },
    "autostart/glance_network": {
        "pattern": r"""dusky_glance\.sh[\\"']*\s+--network(?:["\'\s]|$)""",
        "canonical": 'hl.exec_cmd("~/user_scripts/rofi/dusky_glance.sh --network")',
        "default": False
    },
    "autostart/glance_network_down": {
        "pattern": r"""dusky_glance\.sh[\\"']*\s+--network-down(?:["\'\s]|$)""",
        "canonical": 'hl.exec_cmd("~/user_scripts/rofi/dusky_glance.sh --network-down")',
        "default": False
    },
    "autostart/glance_network_up": {
        "pattern": r"""dusky_glance\.sh[\\"']*\s+--network-up(?:["\'\s]|$)""",
        "canonical": 'hl.exec_cmd("~/user_scripts/rofi/dusky_glance.sh --network-up")',
        "default": False
    },
    "autostart/glance_network_combined": {
        "pattern": r"""dusky_glance\.sh[\\"']*\s+--network-combined(?:["\'\s]|$)""",
        "canonical": 'hl.exec_cmd("~/user_scripts/rofi/dusky_glance.sh --network-combined")',
        "default": False
    },
    "autostart/glance_network_down_session": {
        "pattern": r"""dusky_glance\.sh[\\"']*\s+--network-down-session(?:["\'\s]|$)""",
        "canonical": 'hl.exec_cmd("~/user_scripts/rofi/dusky_glance.sh --network-down-session")',
        "default": False
    },
    "autostart/glance_network_up_session": {
        "pattern": r"""dusky_glance\.sh[\\"']*\s+--network-up-session(?:["\'\s]|$)""",
        "canonical": 'hl.exec_cmd("~/user_scripts/rofi/dusky_glance.sh --network-up-session")',
        "default": False
    },
    "autostart/glance_network_session": {
        "pattern": r"""dusky_glance\.sh[\\"']*\s+--network-session(?:["\'\s]|$)""",
        "canonical": 'hl.exec_cmd("~/user_scripts/rofi/dusky_glance.sh --network-session")',
        "default": False
    },
    "autostart/glance_network_boot_down": {
        "pattern": r"""dusky_glance\.sh[\\"']*\s+--network-boot-down(?:["\'\s]|$)""",
        "canonical": 'hl.exec_cmd("~/user_scripts/rofi/dusky_glance.sh --network-boot-down")',
        "default": False
    },
    "autostart/glance_network_boot_up": {
        "pattern": r"""dusky_glance\.sh[\\"']*\s+--network-boot-up(?:["\'\s]|$)""",
        "canonical": 'hl.exec_cmd("~/user_scripts/rofi/dusky_glance.sh --network-boot-up")',
        "default": False
    },
    "autostart/glance_network_boot": {
        "pattern": r"""dusky_glance\.sh[\\"']*\s+--network-boot(?:["\'\s]|$)""",
        "canonical": 'hl.exec_cmd("~/user_scripts/rofi/dusky_glance.sh --network-boot")',
        "default": False
    },
    "autostart/glance_uptime": {
        "pattern": r"""dusky_glance\.sh[\\"']*\s+--uptime""",
        "canonical": 'hl.exec_cmd("~/user_scripts/rofi/dusky_glance.sh --uptime")',
        "default": False
    },
    "autostart/glance_workspace": {
        "pattern": r"""dusky_glance\.sh[\\"']*\s+--workspace""",
        "canonical": 'hl.exec_cmd("~/user_scripts/rofi/dusky_glance.sh --workspace")',
        "default": False
    },
    "autostart/glance_clock": {
        "pattern": r"""dusky_glance\.sh[\\"']*\s+--clock(?:["\'\s]|$)""",
        "canonical": 'hl.exec_cmd("~/user_scripts/rofi/dusky_glance.sh --clock")',
        "default": False
    },
    "autostart/glance_clock_short": {
        "pattern": r"""dusky_glance\.sh[\\"']*\s+--clock-short""",
        "canonical": 'hl.exec_cmd("~/user_scripts/rofi/dusky_glance.sh --clock-short")',
        "default": False
    },
    "autostart/glance_disk": {
        "pattern": r"""dusky_glance\.sh[\\"']*\s+--disk(?:["\'\s]|$)""",
        "canonical": 'hl.exec_cmd("~/user_scripts/rofi/dusky_glance.sh --disk")',
        "default": False
    },
    "autostart/glance_disk_read": {
        "pattern": r"""dusky_glance\.sh[\\"']*\s+--disk-read""",
        "canonical": 'hl.exec_cmd("~/user_scripts/rofi/dusky_glance.sh --disk-read")',
        "default": False
    },
    "autostart/glance_disk_write": {
        "pattern": r"""dusky_glance\.sh[\\"']*\s+--disk-write""",
        "canonical": 'hl.exec_cmd("~/user_scripts/rofi/dusky_glance.sh --disk-write")',
        "default": False
    },
    "autostart/glance_disk_temp": {
        "pattern": r"""dusky_glance\.sh[\\"']*\s+--disk-temp""",
        "canonical": 'hl.exec_cmd("~/user_scripts/rofi/dusky_glance.sh --disk-temp")',
        "default": False
    },
    "autostart/glance_stopwatch": {
        "pattern": r"""dusky_glance\.sh[\\"']*\s+--stopwatch""",
        "canonical": 'hl.exec_cmd("~/user_scripts/rofi/dusky_glance.sh --stopwatch")',
        "default": False
    },
    "autostart/glance_timer": {
        "pattern": r"""dusky_glance\.sh[\\"']*\s+--timer""",
        "canonical": 'hl.exec_cmd("~/user_scripts/rofi/dusky_glance.sh --timer 15m")',
        "default": False
    },
    "autostart/glance_hud": {
        "pattern": r"""dusky_glance\.sh[\\"']*\s+--hud""",
        "canonical": 'hl.exec_cmd("~/user_scripts/rofi/dusky_glance.sh --hud")',
        "default": False
    },
    "autostart/glance_world_ny": {
        "pattern": r"""dusky_glance\.sh[\\"']*\s+--world-clock\s+America/New_York""",
        "canonical": 'hl.exec_cmd("~/user_scripts/rofi/dusky_glance.sh --world-clock America/New_York NY")',
        "default": False
    },
    "autostart/glance_world_tokyo": {
        "pattern": r"""dusky_glance\.sh[\\"']*\s+--world-clock\s+Asia/Tokyo""",
        "canonical": 'hl.exec_cmd("~/user_scripts/rofi/dusky_glance.sh --world-clock Asia/Tokyo Japan")',
        "default": False
    },
    "autostart/glance_world_london": {
        "pattern": r"""dusky_glance\.sh[\\"']*\s+--world-clock\s+Europe/London""",
        "canonical": 'hl.exec_cmd("~/user_scripts/rofi/dusky_glance.sh --world-clock Europe/London London")',
        "default": False
    },
}

_BOOTSTRAP_PARTS = {
    'systemd_env': re.compile(r'systemctl\s+--user\s+import-environment(?:\s+[A-Z_][A-Z_0-9]*)*'),
    'dbus_env': re.compile(r'dbus-update-activation-environment(?:\s+--[a-z-]+)*'),
    'session_target': re.compile(r'systemctl\s+--user\s+start\s+hyprland-session\.target'),
}
_DISABLED_PART = re.compile(r": dusky-disabled-(systemd_env|dbus_env|session_target) '([^']*)'")


def _visible_command(code):
    # Disabled components remain quoted arguments to the shell's no-op command.
    # Hide them from state detection while keeping their exact text recoverable.
    return _DISABLED_PART.sub(':', code)


def _event_for(key):
    return 'hyprland.shutdown' if key == 'autostart/shutdown_target' else 'hyprland.start'


class AutostartLuaEngine(HyprlandLuaEngine):
    @staticmethod
    def _canonical_command(key, meta):
        """Resolve hardware only when enabling a new dashboard command."""
        gpu_modes = {'glance_gpu_power': '--gpu-power', 'glance_gpu_usage': '--gpu-usage', 'glance_gpu_mem': '--gpu-mem'}
        disk_modes = {'glance_disk_read': '--disk-read', 'glance_disk_write': '--disk-write', 'glance_disk_temp': '--disk-temp'}
        arguments = None
        if key in gpu_modes:
            vendors = {'0x8086': 'intel', '0x1002': 'amd', '0x10de': 'nvidia'}
            for card in sorted(Path('/sys/class/drm').glob('card*')):
                if not re.fullmatch(r'card[0-9]+', card.name):
                    continue
                try:
                    vendor = vendors.get((card / 'device/vendor').read_text(encoding='ascii').strip())
                except OSError:
                    continue
                if vendor:
                    arguments = [gpu_modes[key], card.name, vendor]
                    break
            if arguments is None:
                raise ValueError('No supported DRM GPU found for this dashboard.')
        elif key in disk_modes:
            for device in sorted(Path('/sys/class/block').iterdir()):
                if (device / 'device').exists() and not (device / 'partition').exists():
                    arguments = [disk_modes[key], device.name]
                    break
            if arguments is None:
                raise ValueError('No physical block device found for this dashboard.')
        if arguments is None:
            return meta['canonical']
        command = shlex.join([str(Path.home() / 'user_scripts/rofi/dusky_glance.sh'), *arguments])
        return 'hl.exec_cmd(' + HyprlandLuaEngine._lua_string(command) + ')'

    def _commands(self, content):
        registered = {(c['line'], c['id']) for c in self._call_sites
                      if c['file'] == str(self.config_path) and c['method'] == 'on'}
        regions, entries = {}, []
        for a, b, first, last in calls(content, 'on'):
            ts = tokens(content[first:last])
            event = string_value(ts[0][1]) if ts else None
            if (content.count('\n', 0, a)+1, event) not in registered:
                continue
            all_ts = tokens(content[a:b])
            if len(all_ts) < 2 or all_ts[-2][1] != 'end':
                continue  # Named callbacks remain untouched; add a separate handler if needed.
            regions.setdefault(event, []).append(a+all_ts[-2][2])
            body = content[first:last]
            # Only direct commands in this event callback are managed. A nested
            # function definition does not schedule its commands for startup.
            positions, depths, blocks = [0], [0], []
            function_depth = 0
            for kind, value, _, end in tokens(body):
                if kind == 'name':
                    if value in {'function', 'if', 'do', 'repeat'}:
                        blocks.append(value)
                        function_depth += value == 'function'
                    elif value in {'end', 'until'} and blocks:
                        function_depth -= blocks.pop() == 'function'
                positions.append(end)
                depths.append(function_depth)

            def direct(offset):
                return depths[bisect_right(positions, offset)-1] == 1

            for ca, cb, _, _ in calls(body, 'exec_cmd'):
                if direct(ca):
                    entries.append((event, first+ca, first+cb, body[ca:cb], False))
            # Only explicit command comments inside a registered event are toggles.
            for ca, cb, line in line_comments(body):
                if not direct(ca):
                    continue
                managed = re.fullmatch(r'--\[(=*)\[dusky-autostart-disabled\n(.*)\n\]\1\]', line, re.DOTALL)
                if managed:
                    entries.append((event, first+ca, first+cb, managed[2], 'managed'))
                    continue
                match = re.prefixmatch(r'--[ \t]*(hl\.exec_cmd.*)', line)
                if match and list(calls(match[1], 'exec_cmd')):
                    entries.append((event, first+ca, first+cb, match[1], True))
        return regions, entries

    def load_state(self) -> dict[str, Any]:
        state = super().load_state()
        try:
            content = self.config_path.read_text(encoding='utf-8')
        except OSError:
            content = ''
        _, entries = self._commands(content)
        for key, meta in AUTOSTART_DEFAULTS.items():
            state[key] = any(event == _event_for(key) and not disabled and re.search(meta['pattern'], _visible_command(code), re.DOTALL)
                             for event, _, _, code, disabled in entries)
        return state

    def write_batch(self, changes: list[tuple[str, str, str, str]]) -> tuple[bool, str, str]:
        standard = [c for c in changes if c[1] != 'autostart']
        custom = [c for c in changes if c[1] == 'autostart']
        if not custom:
            return super().write_batch(standard)
        if not self.loaded_files:
            self.load_state()

        def transform(content):
            regions, entries = self._commands(content)
            edits, additions = {}, {}
            for key, _, value, _ in {(c[0], c[1]): c for c in custom}.values():
                full_key = 'autostart/'+key
                if full_key not in AUTOSTART_DEFAULTS:
                    raise ValueError(f'Unknown autostart entry: {key}')
                meta = AUTOSTART_DEFAULTS[full_key]
                event = _event_for(full_key)
                enabled = str(value).lower() in {'true', '1', 'yes', 'on', 't', 'y'}
                handled = set()
                if key in _BOOTSTRAP_PARTS:
                    for e in entries:
                        if e[0] != event or e[4]:
                            continue
                        span = (e[1], e[2])
                        code = edits.get(span, e[3])
                        markers = [m for m in _DISABLED_PART.finditer(code) if m[1] == key]
                        if enabled and markers:
                            edits[span] = _DISABLED_PART.sub(lambda m: m[2] if m[1] == key else m[0], code)
                            handled.add(span)
                        elif not enabled:
                            visible = _visible_command(code)
                            compound = sum(bool(p.search(visible)) for p in _BOOTSTRAP_PARTS.values()) > 1
                            part = _BOOTSTRAP_PARTS[key].search(visible)
                            if part and (compound or _DISABLED_PART.search(code)):
                                # Replace only this component. Keep import ordering, shell
                                # conditions, other services, and the original variable list.
                                edits[span] = _BOOTSTRAP_PARTS[key].sub(
                                    lambda m: ": dusky-disabled-"+key+" '"+m[0]+"'", code, count=1)
                                handled.add(span)
                matches = [e for e in entries if e[0] == event and (e[1], e[2]) not in handled
                           and re.search(meta['pattern'], _visible_command(edits.get((e[1], e[2]), e[3])), re.DOTALL)]
                active = [e for e in matches if not e[4]]
                if enabled and not active and not handled:
                    if matches:
                        managed = [e for e in matches if e[4] == 'managed']
                        for e in managed or matches[:1]:
                            edits[e[1], e[2]] = e[3]
                    else:
                        additions.setdefault(event, []).append(self._canonical_command(key, meta))
                elif not enabled:
                    for e in active:
                        eq = '='
                        while ']'+eq+']' in e[3]:
                            eq += '='
                        edits[e[1], e[2]] = '--['+eq+'[dusky-autostart-disabled\n'+e[3]+'\n]'+eq+']'
            for event, commands in additions.items():
                raw = ''.join('    '+code+'\n' for code in commands)
                if regions.get(event):
                    pos = regions[event][-1]
                    edits[pos, pos] = raw
                else:
                    pos = len(content)
                    edits[pos, pos] = edits.get((pos, pos), '')+'\nhl.on("'+event+'", function()\n'+raw+'end)\n'
            for (a, b), replacement in sorted(edits.items(), reverse=True):
                content = content[:a]+replacement+content[b:]
            return content

        return self.write_transformed(standard, transform)
