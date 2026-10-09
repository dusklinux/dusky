#!/usr/bin/env python3
"""
===============================================================================
DUSKY TUI: SYSTEMD-LOGIND POWER ENGINE
===============================================================================
Engine for Arch Linux (kernel 7.3+, systemd 262+)
Target: /etc/systemd/logind.conf.d/99-power.conf (drop-in)
Base:   /etc/systemd/logind.conf
Features:
  - Shared atomic INI commit, including sudo credential-cache writes
  - Drop-in architecture complying with modern systemd best practices
  - Compile-time default virtualization + base file bridging (zero [Missing] keys)
  - Active override isolation (drop-ins cleanly override base defaults)
  - Automatic systemd-logind configuration reload through systemctl
===============================================================================
"""

lazy from pathlib import Path
lazy from typing import Any

from python.engines.bridged_ini import BridgedIniEngine
from python.shared.config_io import read_text


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
        try:
            text, _ = read_text(path)
            return self._parse(text, include_comments=include_commented)
        except (OSError, UnicodeError) as exc:
            print(f'[SystemdPowerEngine] Could not parse {path}: {exc}')
            return {}

    def load_state(self) -> dict[str, Any]:
        with self._lock:
            return self._load_state()

    def _load_state(self) -> dict[str, Any]:
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

        # Tier 3: Record the same descriptor snapshot used to parse the target.
        self._loaded = False
        try:
            text, self._snapshot = read_text(self.config_path)
            dropin_entries = self._parse(text, include_comments=False)
            for full_k, value in dropin_entries.items():
                scope, _, key = full_k.partition('/')
                target_scope = 'Login' if scope in ('DEFAULT', 'Login') else scope
                state[f'{target_scope}/{key}'] = str(value)
            self._loaded = True
        except (OSError, UnicodeError) as exc:
            print(f'[SystemdPowerEngine] Could not read target: {exc}')

        # Mirror bare keys for unambiguous root lookups
        for k, v in list(state.items()):
            bare = k.split("/")[-1]
            if bare not in state:
                state[bare] = v

        self.cache = state
        return self.cache

    def write_batch(self, changes: list[tuple[str, str, str, str]]) -> tuple[bool, str, str]:
        with self._lock:
            return self._write_power_batch(changes)

    def _write_power_batch(self, changes):
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

        # Delegate atomic mutation to IniConfigEngine
        success, msg, debug = super().write_batch(config_changes)

        if not success:
            return False, msg, debug

        self._load_state()
        # The parent performs the documented systemctl reload once per commit.
        return True, msg, debug
