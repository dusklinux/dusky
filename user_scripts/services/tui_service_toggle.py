import subprocess

from python.frontend.core_types import ConfigItem

ENGINE_TYPE = "systemd"
TARGET_FILE = "/etc/systemd/system"
APP_TITLE = "Dusky Service Manager"
DEFAULT_MODE = "auto"
THEME_FILE = "~/.config/matugen/generated/dusky_tui.json"


TABS = [
    "Core User",
    "Core System",
    "Active",
    "Enabled",
    "Timers",
    "All User",
    "All System",
]

SCHEMA = {i: [] for i in range(len(TABS))}

# --- DETAILED EXTENDED HELP DICTIONARIES ---
CORE_USER_DEFS = {
    "hyprsunset.service": (
        "Night Light (Blue Light Filter)",
        "Manages hyprsunset, a Wayland-native blue light filter. Turning this on will adjust the color temperature of your display to reduce eye strain at night.",
    ),
    "dusky_battery.service": (
        "Battery Level Notifications",
        "Background daemon that monitors your battery level and sends desktop notifications using libnotify when power is running low.",
    ),
    "network_meter.service": (
        "Waybar Network Traffic Monitor",
        "Service to track network traffic. Often used in conjunction with Waybar to display real-time upload and download speeds.",
    ),
    "dusky.service": (
        "Dusky Background Service",
        "The primary Dusky ecosystem background service. Handles core daemon tasks required for the environment.",
    ),
    "dusky_quickpanal.service": (
        "Dusky quickpanal Service",
        "Manages the Dusky quick access panel (Quickpanal) overlay.",
    ),
    "update_checker.timer": (
        "Automatic Update Checker",
        "Periodically checks your package manager for system updates and caches the result for your status bar.",
    ),
    "hypridle.service": (
        "Hyprland Idle Daemon",
        "Hyprland's idle management daemon. Handles screen dimming, locking, and DPMS sleep states when you are away from the computer.",
    ),
    "osd_lock.service": (
        "OSD for CapsLock,NumLock,ScrollLock",
        "On-Screen Display service for hardware lock keys. Shows a visual pop-up when Caps Lock, Num Lock, or Scroll Lock is toggled.",
    ),
    "hyprpolkitagent.service": (
        "(Polkit) Root Password Prompt",
        "The authentication agent for Hyprland. This is what prompts you for a password when an app requests root access (like pkexec).",
    ),
    "dusky_ram_monitor.service": (
        "Dusky RAM Monitor Daemon",
        "Background monitor that alerts you if physical RAM usage exceeds 95% or ZRAM swap occupancy exceeds 90%. Clicking the alert opens an interactive Rofi menu to select and terminate memory-heavy processes before a system crash.",
    ),
    "dusky_visualizer.service": (
        "Audio Visualizer Daemon",
        "Background daemon for the audio visualizer. Renders visualizer shapes dynamically in the background.",
    ),
    "dusky_screentime.service": (
        "Dusky Screentime Tracking Daemon",
        "Wayland screentime tracking daemon. Connects to Hyprland UNIX socket to monitor active window durations and persist daily usage metrics.",
    ),
}

CORE_SYSTEM_DEFS = {
    "vsftpd.service": (
        "FTP Server (vsftpd)",
        "Very Secure FTP Daemon. Manages the FTP server for file transfers. Only enable this if you actively need to host an FTP server.",
    ),
    "tlp.service": (
        "TLP Power Management",
        "Advanced power management for Linux. Applies various battery-saving tweaks to the kernel, PCI, and USB devices.",
    ),
    "dusky_cpu.service": (
        "Dusky CPU Cores & Power Restorer",
        "Restores your custom CPU core states and package power limit adjustments dynamically on system boot.",
    ),
    "numlock_disable.service": (
        "Disable NumLock on TTY Boot",
        "Disables NumLock on virtual consoles (TTYs 1 to 6) during boot. Useful for keyboards that default to NumLock ON, preventing lock-out at the login screen.",
    ),
    "swayosd-libinput-backend.service": (
        "SwayOSD Input Backend",
        "Backend service for SwayOSD. Handles raw libinput events to render volume/brightness overlays without relying on the window manager.",
    ),
    "sshd.service": (
        "SSH Server (OpenSSH)",
        "OpenSSH server daemon. Allows remote access to this machine via SSH. Ensure your firewall is configured if exposing this to the internet.",
    ),
    "warp-svc.service": (
        "Cloudflare WARP VPN",
        "Cloudflare WARP daemon. Provides a fast, secure VPN tunnel using WireGuard to route your DNS and internet traffic.",
    ),
    "firewalld.service": (
        "Firewall (firewalld)",
        "Dynamic firewall manager. Provides a D-Bus interface to manage firewall rules and network zones.",
    ),
    "tailscaled.service": ("Tailscaled", "Allows remote access"),
    "dusky_snapshot.timer": (
        "8 PM Daily Snapshots (Backup)",
        "Triggers a snapshot automaticaly everyday at 8PM, while automatically cleaning up the oldest snapshot (max 6).",
    ),
    "zram-recompress.timer": (
        "ZRAM 15M Cold Pages Compressor",
        "Auto compresses cold pages in both zram0 and zram1 with zstd level 3 every 15 minutes to reclaim memory",
    ),
    "dusky_boot_mem_reclaim.timer": (
        "1Min Boot Memory Reclaimer",
        "Oneshot boot reclaimer timer. Triggers exactly 1 minute after boot to compress and swap cold initialization memory to ZRAM swap, reducing the startup memory footprint.",
    ),
    "ufw.service": (
        "Firewall (UFW)",
        "Uncomplicated Firewall. A user-friendly front-end for iptables to manage network access rules.",
    ),
}

import concurrent.futures


# =============================================================================
# FAST TARGETED CORE FETCH (Tabs 0-1)
# Only queries the ~22 hardcoded units instead of enumerating ALL installed units.
# =============================================================================
def _fetch_core_installed(scope: str, units: list[str]) -> set:
    """Checks only specific units for existence via targeted list-unit-files query."""
    if not units:
        return set()
    call = ["systemctl", "list-unit-files", "--no-pager", "--no-legend"] + units
    if scope == "user":
        call.insert(1, "--user")
    try:
        res = subprocess.run(
            call, capture_output=True, text=True, stdin=subprocess.DEVNULL
        )
        installed = set()
        for line in res.stdout.splitlines():
            if not line:
                continue
            parts = line.split()
            if parts:
                installed.add(parts[0])
        return installed
    except Exception as e:
        import sys

        print(
            f"[tui_service_toggle] ERROR: _fetch_core_installed({scope}): {e}",
            file=sys.stderr,
        )
        return set()


# Fast path: only query the specific hardcoded units (2 subprocess calls, ~22 units)
_core_user_units = list(CORE_USER_DEFS.keys())
_core_sys_units = list(CORE_SYSTEM_DEFS.keys())

with concurrent.futures.ThreadPoolExecutor(max_workers=2) as _fast_exec:
    _f_core_user = _fast_exec.submit(_fetch_core_installed, "user", _core_user_units)
    _f_core_sys = _fast_exec.submit(_fetch_core_installed, "system", _core_sys_units)

    _core_installed_user = _f_core_user.result()
    _core_installed_sys = _f_core_sys.result()

# --- TAB 0: CORE USER (instant) ---
for unit, (label, help_text) in CORE_USER_DEFS.items():
    if unit in _core_installed_user:
        SCHEMA[0].append(
            ConfigItem(
                label=label,
                key=unit,
                scope="user",
                type_="bool",
                default=False,
                extended_help=f"**Unit:** `{unit}`\n**Scope:** User\n\n{help_text}",
            )
        )

# --- TAB 1: CORE SYSTEM (instant) ---
for unit, (label, help_text) in CORE_SYSTEM_DEFS.items():
    if unit in _core_installed_sys:
        SCHEMA[1].append(
            ConfigItem(
                label=label,
                key=unit,
                scope="system",
                type_="bool",
                default=False,
                extended_help=f"**Unit:** `{unit}`\n**Scope:** System\n\n{help_text}",
            )
        )


# =============================================================================
# DEFERRED FULL FETCH (Tabs 2-6)
# Background threads start immediately (running in parallel with TUI startup).
# The TUI calls DEFERRED_LOAD() after initial render to complete these tabs.
# =============================================================================
def _fetch_all_unit_files(scope: str) -> tuple[set, set, set]:
    """Returns (installed_services, enabled_services, installed_timers) in a single pass."""
    call = [
        "systemctl",
        "list-unit-files",
        "--type=service,timer",
        "--no-pager",
        "--no-legend",
    ]
    if scope == "user":
        call.insert(1, "--user")

    installed_srv = set()
    enabled_srv = set()
    installed_tmr = set()

    try:
        res = subprocess.run(
            call, capture_output=True, text=True, stdin=subprocess.DEVNULL
        )
        for line in res.stdout.splitlines():
            if not line:
                continue
            parts = line.split()
            if len(parts) < 2:
                continue
            unit, state = parts[0], parts[1]

            if unit.endswith(".service"):
                installed_srv.add(unit)
                if state == "enabled":
                    enabled_srv.add(unit)
            elif unit.endswith(".timer"):
                installed_tmr.add(unit)

        return installed_srv, enabled_srv, installed_tmr
    except Exception as e:
        import sys

        print(
            f"[tui_service_toggle] ERROR: _fetch_all_unit_files({scope}): {e}",
            file=sys.stderr,
        )
        return set(), set(), set()


def _fetch_active_services(scope: str) -> set:
    call = [
        "systemctl",
        "list-units",
        "--type=service",
        "--state=active",
        "--no-pager",
        "--no-legend",
    ]
    if scope == "user":
        call.insert(1, "--user")
    try:
        res = subprocess.run(
            call, capture_output=True, text=True, stdin=subprocess.DEVNULL
        )
        return {line.split()[0] for line in res.stdout.splitlines() if line}
    except Exception as e:
        import sys

        print(
            f"[tui_service_toggle] ERROR: _fetch_active_services({scope}): {e}",
            file=sys.stderr,
        )
        return set()


# Start full fetch in background IMMEDIATELY — these threads run in parallel
# with TUI startup so they're often already finished by the time DEFERRED_LOAD is called.
_bg_executor = concurrent.futures.ThreadPoolExecutor(max_workers=4)
_f_user_all = _bg_executor.submit(_fetch_all_unit_files, "user")
_f_sys_all = _bg_executor.submit(_fetch_all_unit_files, "system")
_f_user_act = _bg_executor.submit(_fetch_active_services, "user")
_f_sys_act = _bg_executor.submit(_fetch_active_services, "system")


def DEFERRED_LOAD() -> list[int]:
    """
    Completes the deferred tab population for tabs 2-6.
    Waits on background futures (which have been running since module import),
    then populates the SCHEMA lists.
    Returns list of tab indices that were populated.
    Called by the TUI after its initial render of tabs 0-1.
    """
    installed_user_srv, enabled_user, timers_user = _f_user_all.result()
    installed_sys_srv, enabled_sys, timers_sys = _f_sys_all.result()
    active_user_raw = _f_user_act.result()
    active_sys_raw = _f_sys_act.result()
    _bg_executor.shutdown(wait=False)

    installed_user = installed_user_srv | timers_user
    installed_sys = installed_sys_srv | timers_sys

    active_user = active_user_raw.intersection(installed_user_srv)
    active_sys = active_sys_raw.intersection(installed_sys_srv)

    # Track used units (core tabs already populated, avoid duplicates in "All" tabs)
    used_user = {unit for unit in CORE_USER_DEFS if unit in _core_installed_user}
    used_sys = {unit for unit in CORE_SYSTEM_DEFS if unit in _core_installed_sys}

    # --- TAB 2: ACTIVE SERVICES ---
    for unit in sorted(active_user):
        if "@" in unit:
            continue
        SCHEMA[2].append(
            ConfigItem(
                label=unit,
                key=unit,
                scope="user",
                type_="bool",
                default=False,
                group="User Services",
                extended_help=f"**Unit:** `{unit}`\n**Scope:** User\n\nCurrently active user-level service.",
            )
        )

    for unit in sorted(active_sys):
        if "@" in unit:
            continue
        SCHEMA[2].append(
            ConfigItem(
                label=unit,
                key=unit,
                scope="system",
                type_="bool",
                default=False,
                group="System Services",
                extended_help=f"**Unit:** `{unit}`\n**Scope:** System\n\nCurrently active system-level service.",
            )
        )

    # --- TAB 3: ENABLED SERVICES ---
    for unit in sorted(enabled_user):
        if "@" in unit:
            continue
        SCHEMA[3].append(
            ConfigItem(
                label=unit,
                key=unit,
                scope="user",
                type_="bool",
                default=False,
                group="User Services",
                extended_help=f"**Unit:** `{unit}`\n**Scope:** User\n\nEnabled to start automatically on boot.",
            )
        )

    for unit in sorted(enabled_sys):
        if "@" in unit:
            continue
        SCHEMA[3].append(
            ConfigItem(
                label=unit,
                key=unit,
                scope="system",
                type_="bool",
                default=False,
                group="System Services",
                extended_help=f"**Unit:** `{unit}`\n**Scope:** System\n\nEnabled to start automatically on boot.",
            )
        )

    # --- TAB 4: TIMERS ---
    for unit in sorted(timers_user):
        SCHEMA[4].append(
            ConfigItem(
                label=unit,
                key=unit,
                scope="user",
                type_="bool",
                default=False,
                group="User Timers",
                extended_help=f"**Unit:** `{unit}`\n**Scope:** User\n\nSystemd timer unit (Cron alternative).",
            )
        )
        used_user.add(unit)

    for unit in sorted(timers_sys):
        SCHEMA[4].append(
            ConfigItem(
                label=unit,
                key=unit,
                scope="system",
                type_="bool",
                default=False,
                group="System Timers",
                extended_help=f"**Unit:** `{unit}`\n**Scope:** System\n\nSystemd timer unit (Cron alternative).",
            )
        )
        used_sys.add(unit)

    # --- TAB 5: ALL USER ---
    for unit in sorted(installed_user - used_user):
        if "@" in unit or not unit.endswith(".service"):
            continue
        SCHEMA[5].append(
            ConfigItem(
                label=unit,
                key=unit,
                scope="user",
                type_="bool",
                default=False,
                group=unit[0].upper(),
                extended_help=f"**Unit:** `{unit}`\n**Scope:** User\n\nAuto-discovered service.",
            )
        )

    # --- TAB 6: ALL SYSTEM ---
    for unit in sorted(installed_sys - used_sys):
        if "@" in unit or not unit.endswith(".service"):
            continue
        SCHEMA[6].append(
            ConfigItem(
                label=unit,
                key=unit,
                scope="system",
                type_="bool",
                default=False,
                group=unit[0].upper(),
                extended_help=f"**Unit:** `{unit}`\n**Scope:** System\n\nAuto-discovered service.",
            )
        )

    return [2, 3, 4, 5, 6]
