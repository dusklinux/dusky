#!/usr/bin/env python3
"""
===============================================================================
DUSKY TUI: SYSTEMD-LOGIND POWER ENGINE
===============================================================================
Engine for Modern Arch Linux (Kernel 7.2+, systemd 257+)
Target: /etc/systemd/logind.conf.d/99-power.conf (drop-in)
Base:   /etc/systemd/logind.conf
Features:
  - Strict POSIX atomicity (tempfile + os.replace / sudo tee fallback)
  - Drop-in architecture complying with modern systemd best practices
  - Compile-time default virtualization + base file bridging (zero [Missing] keys)
  - Active override isolation (drop-ins cleanly override base defaults)
  - Automatic systemd-logind daemon configuration reload (SIGHUP / systemctl)
===============================================================================
"""

import os
import re
lazy import stat
lazy import subprocess
lazy import tempfile
lazy from pathlib import Path
lazy from typing import Any

from python.engines.bridged_ini import BridgedIniEngine


class SystemdPowerEngine(BridgedIniEngine):
    """
    High-performance drop-in configuration engine for systemd-logind.
    Virtualizes compile-time defaults, bridges /etc/systemd/logind.conf,
    and isolates user modifications into /etc/systemd/logind.conf.d/99-power.conf.
    """

    DEFAULT_TARGET = "/etc/systemd/logind.conf.d/99-power.conf"
    BASE_CONFIG = "/etc/systemd/logind.conf"

    # Upstream compile-time defaults for systemd-logind (Arch Linux systemd 257+)
    LOGIND_COMPILE_DEFAULTS: dict[str, Any] = {
        "HandlePowerKey": "poweroff",
        "HandlePowerKeyLongPress": "ignore",
        "HandleRebootKey": "reboot",
        "HandleRebootKeyLongPress": "poweroff",
        "HandleSuspendKey": "suspend",
        "HandleSuspendKeyLongPress": "hibernate",
        "HandleHibernateKey": "hibernate",
        "HandleHibernateKeyLongPress": "ignore",
        "HandleLidSwitch": "suspend",
        "HandleLidSwitchExternalPower": "suspend",
        "HandleLidSwitchDocked": "ignore",
        "HoldoffTimeoutSec": "30s",
        "IdleAction": "ignore",
        "IdleActionSec": "30min",
        "SleepOperation": "suspend-then-hibernate suspend",
        "PowerKeyIgnoreInhibited": "no",
        "SuspendKeyIgnoreInhibited": "no",
        "HibernateKeyIgnoreInhibited": "no",
        "LidSwitchIgnoreInhibited": "yes",
        "RebootKeyIgnoreInhibited": "no",
        "InhibitDelayMaxSec": "5",
        "UserStopDelaySec": "10s",
        "KillUserProcesses": "no",
        "KillExcludeUsers": "root",
        "ReserveVT": "6",
        "NAutoVTs": "6",
        "RemoveIPC": "yes",
        "StopIdleSessionSec": "infinity",
    }

    def __init__(self, config_path: str = DEFAULT_TARGET):
        super().__init__(config_path=config_path)
        self.base_config_path = Path(self.BASE_CONFIG).resolve()
        self.dropin_dir = self.config_path.parent

    def _parse_ini_lines(self, path: Path, include_commented: bool = True) -> dict[str, Any]:
        """
        Parses INI entries from a path with optional dormant (commented) default recovery.
        Active entries always take precedence over commented ones.
        """
        if not path.exists():
            return {}

        results: dict[str, Any] = {}
        authoritative: set[str] = set()
        current_scope = "DEFAULT"

        try:
            with open(path, "r", encoding="utf-8") as f:
                for line in f:
                    sec_match = self._RE_SECTION.prefixmatch(line)
                    if sec_match:
                        current_scope = sec_match.group(1).strip()
                        continue

                    match = self._RE_KEY.prefixmatch(line.rstrip("\n"))
                    if match:
                        ws1, cmt, ws2, key, assign_op, val = match.groups()
                        full_key = f"{current_scope}/{key}"

                        if not include_commented and cmt:
                            continue

                        if full_key in authoritative and cmt:
                            continue

                        if assign_op is not None:
                            v = val.strip()
                            if v.startswith('"') and v.endswith('"') and len(v) >= 2:
                                v = v[1:-1]
                            results[full_key] = v
                        else:
                            results[full_key] = True

                        if not cmt:
                            authoritative.add(full_key)

        except (OSError, IOError) as e:
            print(f"[SystemdPowerEngine] Warning: Could not parse {path}: {e}")

        return results

    def load_state(self) -> dict[str, Any]:
        """
        Constructs the unified configuration state using a three-tier hierarchy:
          Tier 1: Upstream compile-time defaults (virtualized)
          Tier 2: Base /etc/systemd/logind.conf (active + dormant defaults)
          Tier 3: Drop-in /etc/systemd/logind.conf.d/*.conf (highest priority overrides)
        """
        state: dict[str, Any] = {}

        # Tier 1: Compile-time defaults
        for key, val in self.LOGIND_COMPILE_DEFAULTS.items():
            state[f"Login/{key}"] = str(val)

        # Tier 2: Bridge base /etc/systemd/logind.conf (both active and commented defaults)
        base_entries = self._parse_ini_lines(self.base_config_path, include_commented=True)
        for full_k, v in base_entries.items():
            scope, _, k = full_k.partition("/")
            target_scope = "Login" if scope in ("DEFAULT", "Login") else scope
            state[f"{target_scope}/{k}"] = str(v)

        # Tier 3: Drop-in file active overrides (only active lines supersede base)
        if self.config_path.exists():
            try:
                self.file_mtime = self.config_path.stat().st_mtime
            except OSError:
                self.file_mtime = 0.0

            dropin_entries = self._parse_ini_lines(self.config_path, include_commented=False)
            for full_k, v in dropin_entries.items():
                scope, _, k = full_k.partition("/")
                target_scope = "Login" if scope in ("DEFAULT", "Login") else scope
                state[f"{target_scope}/{k}"] = str(v)
        else:
            self.file_mtime = 0.0

        # Mirror bare keys for unambiguous root lookups
        for k, v in list(state.items()):
            bare = k.split("/")[-1]
            if bare not in state:
                state[bare] = v

        self.cache = state
        return self.cache

    def write_batch(self, changes: list[tuple[str, str, str, str]]) -> tuple[bool, str, str]:
        """
        Writes batched power configuration changes strictly to the drop-in file.
        Ensures [Login] section header, applies atomic commit, and reloads systemd-logind.
        """
        if not changes:
            return True, "No pending changes.", ""

        # Filter out purely interactive diagnostic actions
        config_changes: list[tuple[str, str, str, str]] = []
        for key, scope, val, itype in changes:
            if itype == "action":
                continue
            # Systemd logind configurations strictly reside under [Login]
            norm_scope = "Login" if scope in ("DEFAULT", "Login", "") else scope
            config_changes.append((key, norm_scope, val, itype))

        if not config_changes:
            return True, "Actions processed.", ""

        # Guarantee the drop-in directory exists
        try:
            self.dropin_dir.mkdir(parents=True, exist_ok=True)
        except PermissionError:
            try:
                subprocess.run(["sudo", "-n", "mkdir", "-p", str(self.dropin_dir)], check=True, capture_output=True)
            except Exception:
                return False, "AUTH_REQUIRED", "Cannot create drop-in directory."

        # Pre-seed drop-in header if file doesn't exist yet
        if not self.config_path.exists():
            header = (
                "# =============================================================================\n"
                "# Managed strictly by Dusky TUI - Systemd Power Manager\n"
                "# Target: systemd-logind drop-in configuration\n"
                "# =============================================================================\n"
                "[Login]\n"
            )
            try:
                with open(self.config_path, "w", encoding="utf-8") as f:
                    f.write(header)
                self.config_path.chmod(stat.S_IRUSR | stat.S_IWUSR | stat.S_IRGRP | stat.S_IROTH)
                self.file_mtime = self.config_path.stat().st_mtime
            except PermissionError:
                try:
                    res = subprocess.run(
                        ["sudo", "-n", "tee", str(self.config_path)],
                        input=header.encode("utf-8"),
                        capture_output=True,
                        timeout=5
                    )
                    if res.returncode != 0:
                        return False, "AUTH_REQUIRED", ""
                    self.file_mtime = self.config_path.stat().st_mtime
                except Exception:
                    return False, "AUTH_REQUIRED", ""

        # Delegate atomic mutation to IniConfigEngine
        success, msg, debug = super().write_batch(config_changes)

        if not success:
            return False, msg, debug

        # Ensure world-readable permissions (0644) for systemd drop-in
        if self.config_path.exists():
            try:
                self.config_path.chmod(stat.S_IRUSR | stat.S_IWUSR | stat.S_IRGRP | stat.S_IROTH)
            except PermissionError:
                try:
                    subprocess.run(["sudo", "-n", "chmod", "0644", str(self.config_path)], check=False, capture_output=True)
                except Exception:
                    pass

        # Reload systemd-logind daemon to enact changes immediately
        reload_msg = self._reload_logind()

        return True, f"{msg} {reload_msg}".strip(), debug

    def _reload_logind(self) -> str:
        """Reloads systemd-logind via systemctl or SIGHUP fallback."""
        try:
            res = subprocess.run(
                ["systemctl", "reload", "systemd-logind.service"],
                capture_output=True,
                text=True,
                timeout=5
            )
            if res.returncode == 0:
                return "[systemd-logind reloaded]"

            # SIGHUP fallback for non-systemctl environments or restricted polkit
            subprocess.run(["pkill", "-HUP", "-x", "systemd-logind"], capture_output=True, timeout=5)
            return "[systemd-logind reloaded via SIGHUP]"
        except Exception as e:
            return f"[reload notice: {e}]"
