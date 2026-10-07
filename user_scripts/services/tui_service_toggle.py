#!/usr/bin/env python3
import subprocess
import sys
from pathlib import Path

_DUSKY_TUI_ROOT = Path(__file__).resolve().parents[1] / "dusky_tui"
if str(_DUSKY_TUI_ROOT) not in sys.path:
    sys.path.insert(0, str(_DUSKY_TUI_ROOT))

from python.frontend.core_types import ConfigItem
from python.engines.systemd import SystemdEngine, ENABLED_STATES, MANAGEABLE_STATES

if __name__ == "__main__":
    script_path = Path(__file__).resolve()
    main_router = _DUSKY_TUI_ROOT / "python" / "main" / "main.py"
    if not main_router.exists():
        print(f"[-] Error: Main Dusky TUI router not found at {main_router}", file=sys.stderr)
        sys.exit(1)
    sys.exit(subprocess.run([sys.executable, str(main_router), str(script_path), *sys.argv[1:]]).returncode)

ENGINE_TYPE = "systemd"
HIDE_MISSING_ITEMS = True
TARGET_FILE = "/etc/systemd/system"
APP_TITLE = "Dusky Service Manager"
DEFAULT_MODE = "auto"
THEME_FILE = "~/.config/matugen/generated/dusky_tui.json"
ENABLE_USER_PRESETS = True
USER_PRESETS_TAB = "Presets"
TAB_NOTICES = {
    2: {
        "level": "info",
        "message": "These services are running. Switches show startup enablement; a running service may have its switch off.",
    },
    8: {
        "level": "info",
        "message": "Read-only units, grouped by systemd state. **Static:** no ordinary enable switch. **Generated:** made automatically. **Transient:** created at runtime. Press **?** for details about the selected unit's state.",
    }
}


TABS = [
    "Core User",
    "Core System",
    "Active",
    "Enabled",
    "Timers",
    "All User",
    "All System",
    "Presets",
    "Read Only",
]

SCHEMA = {i: [] for i in range(len(TABS))}

READ_ONLY_STATE_HELP = {
    "static": "No ordinary enable switch; another unit or activation mechanism can start it.",
    "generated": "Created automatically by a systemd generator; it cannot be enabled directly.",
    "transient": "Created at runtime through systemd's API; it cannot be enabled directly.",
    "alias": "Another name for a different unit; manage the original unit instead.",
    "masked": "Blocked from starting until the mask is removed.",
    "masked-runtime": "Blocked from starting for this boot until the runtime mask is removed.",
    "linked": "Made available through a symlink to a unit file outside the usual search path.",
    "linked-runtime": "Made available through a temporary symlink to an external unit file.",
    "bad": "Systemd found an invalid unit file or could not determine its state.",
}

# --- DETAILED EXTENDED HELP DICTIONARIES ---
CORE_USER_DEFS = {
    "dusky_vnc_desktop.service": (
        "VNC Desktop Sharing",
        "Shares the active Hyprland desktop with a VNC viewer on another phone, tablet or PC over a local network or Tailscale. The switch enables and starts the server, or disables and stops it.",
    ),
    "dusky_vnc_display.service": (
        "VNC Secondary Display",
        "Creates a landscape or portrait Hyprland monitor for another phone, tablet or PC and serves it with WayVNC on port 5901. Set orientation with second_display.py orientation portrait|landscape. Disabling stops the server and removes the virtual monitor.",
    ),
    "dusky_moonlight_display.service": (
        "Moonlight Secondary Display",
        "Creates its own landscape or portrait Hyprland monitor and streams it through Sunshine to Moonlight on another phone, tablet or PC. Set orientation with moonlight_setup.py orientation portrait|landscape. Disabling stops Sunshine and removes this monitor.",
    ),
    "app-dev.lizardbyte.app.Sunshine.service": (
        "Sunshine Default Service",
        "Streams the default desktop through Sunshine. It uses the same Moonlight ports as Moonlight Secondary Display, so run only one of these Sunshine services at a time.",
    ),
    "hyprsunset.service": (
        "Night Light",
        "Manages hyprsunset, a Wayland-native blue light filter. Turning this on will adjust the color temperature of your display to reduce eye strain at night.",
    ),
    "dusky_battery.service": (
        "Battery Alerts",
        "Background daemon that monitors your battery level and sends desktop notifications using libnotify when power is running low.",
    ),
    "network_meter.service": (
        "Network Traffic Meter",
        "Service to track network traffic. Often used in conjunction with Waybar to display real-time upload and download speeds.",
    ),
    "dusky.service": (
        "Control Center Preload",
        "Autostarts Control center to open it faster on the first invocation, second invocation is always the same",
    ),
    "update_checker.timer": (
        "Automatic Update Checker",
        "Checks local main against origin/main, then refreshes cached Pacman, AUR, and Dusky commit counts for the panels and status bar. Runs 10 minutes after the user manager starts, then every 6 hours after completion, with up to 10 minutes of jitter. Toggle this timer; its static companion service is started by the timer.",
    ),
    "hypridle.service": (
        "Hyprland Idle Daemon",
        "Hyprland's idle management daemon. Handles screen dimming, locking, and DPMS sleep states when you are away from the computer.",
    ),
    "osd_lock.service": (
        "Lock Key OSD",
        "On-Screen Display service for hardware lock keys. Shows a visual pop-up when Caps Lock, Num Lock, or Scroll Lock is toggled.",
    ),
    "dusky_polkit.service": (
        "Dusky Polkit",
        "Lightweight Python/Rich Polkit agent. Prompts for root/admin password on privilege escalation (like pkexec).",
    ),
    "dusky_clipboard.service": (
        "Dusky Clipboard Manager",
        "Unified Wayland clipboard history and persistence daemon (cliphist + wl-clip-persist). Seamlessly records copied text and images to SQLite history, preserves clipboard selections even after source apps close, and supports live RAM/disk persistence switching without reboot.",
    ),
    "dusky_vdagent.service": (
        "VM Clipboard Sharing",
        "Shares text and images between this Wayland guest and its host through the virt-manager or virt-viewer SPICE console. Enable to start clipboard sharing with graphical sessions; disable to stop it. File sharing is handled separately by the guest's virtiofs mount.",
    ),
    "dusky_ram_monitor.service": (
        "Dusky RAM Monitor",
        "Background monitor that alerts you if RAM usage reaches 95%, or both RAM usage and active ZRAM swap occupancy reach 90%. Clicking the alert opens an interactive Rofi menu to select and terminate memory-heavy processes before a system crash.",
    ),
    "dusky_visualizer.service": (
        "Audio Visualizer Daemon",
        "Background daemon for the audio visualizer. Renders visualizer shapes dynamically in the background.",
    ),
    "dusky_screentime.service": (
        "Screentime Tracker",
        "Wayland screentime tracking daemon. Connects to Hyprland UNIX socket to monitor active window durations and persist daily usage metrics.",
    ),

    "dusky_notif_time.service": (
        "Notification Timestamps",
        "Background daemon that records first-observed timestamps for Mako desktop notifications and caches them for Dusky Tray and Rofi displays.",
    ),
    "modprobed-db.service": (
        "Hardware Profiler",
        "Records used kernel modules to `~/.config/modprobed.db` for `localmodconfig`. Keep enabled for lean native kernels.",
    ),
    "modprobed-db.timer": (
        "Profiler Timer",
        "Triggers profiler every 6h to refresh hardware DB. Enabled via service.",
    ),
    "dusky_llm.service": (
        "LLM Service (dusky_llm)",
        "Local LLM inference daemon (Ollama / llama.cpp wrapper). Handles prompt completion and embeddings for Dusky AI features.",
    ),
    "dusky_stt.service": (
        "STT (Parakeet GPU)",
        "Speech-to-text daemon (NVIDIA Parakeet 0.6B, on-demand CUDA worker). ON = warm-resident: model preloaded, instant dictation, VRAM held (plugged-in mode). OFF = on-demand: hotkey still works, VRAM held only mid-job, then worker exits and the service stops itself so the dGPU can sleep (battery mode).",
    ),
    "dusky_firefox_cache.service": (
        "Firefox Profile RAM Sync",
        "Synchronizes Firefox profiles into RAM (tmpfs) to eliminate SSD write amplification from cookie and SQLite churn. Automatically restores to disk on shutdown, with periodic background resyncs.",
    ),
    "dusky_firefox_cache_resync.timer": (
        "Firefox RAM Cache Resync",
        "Runs the Firefox profile RAM sync every hour while the companion service is active.",
    ),
    "dusky_oom_shield.service": (
        "Dusky OOM Shield",
        "Protects the active Hyprland session and pinned windows from systemd-oomd pressure kills.",
    ),
    "wireplumber.service": (
        "WirePlumber Audio Session",
        "Session and policy manager for PipeWire. Handles audio streams, hardware device switching, Bluetooth audio endpoints, and volume persistence.",
    ),
    "gamemoded.service": (
        "GameMode Optimizer",
        "Feral Interactive GameMode daemon. Temporarily prioritizes CPU frequency governors, I/O scheduling, and GPU performance while gaming.",
    ),
}

CORE_SYSTEM_DEFS = {
    "docker.service": (
        "Docker Engine",
        "Runs and manages Docker containers. The switch controls daemon startup enablement and starts or stops the service.",
    ),
    "vsftpd.service": (
        "FTP Server (vsftpd)",
        "Very Secure FTP Daemon. Manages the FTP server for file transfers. Only enable this if you actively need to host an FTP server.",
    ),
    "tlp.service": (
        "TLP Power Management",
        "Advanced power management for Linux. Applies various battery-saving tweaks to the kernel, PCI, and USB devices.",
    ),
    "battery-charge-limit.service": (
        "Battery Charge Limit",
        "Applies an 80% hardware battery charge limit at boot.",
    ),
    "dusky_cpu.service": (
        "CPU Power Restorer",
        "Restores your custom CPU core states and package power limit adjustments dynamically on system boot.",
    ),
    "dusky_kbd_backlight.service": (
        "Keyboard Backlight State",
        "Restores the configured keyboard backlight hardware state at boot.",
    ),
    "ghelper-gpu-boot.service": (
        "G-Helper GPU at Boot",
        "Applies the configured G-Helper GPU mode during system startup.",
    ),
    "glance_cpu_pkg_watt.service": (
        "Dusky Glance",
        "Allows Dusky Glance to read CPU package energy counters.",
    ),
    "numlock_disable.service": (
        "NumLock on TTY Boot",
        "Disables NumLock on virtual consoles (TTYs 1 to 6) during boot. Useful for keyboards that default to NumLock ON, preventing lock-out at the login screen.",
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
        "Dynamic firewall manager. Provides a D-Bus interface to manage network zones and packet filtering rules.",
    ),
    "tailscaled.service": ("Tailscaled", "Allows remote access"),
    "dusky_snapshot.timer": (
        "Root + Home Snapshots",
        "Creates paired root and home snapshots daily at 8 PM and keeps up to six scheduled pairs.",
    ),
    "dusky_zram_recompress.timer": (
        "ZRAM Recompression",
        "Recompresses idle ZRAM pages every hour while the timer is enabled.",
    ),
    "dusky_boot_zram_flush.timer": (
        "ZRAM Boot Flush Timer",
        "One-shot boot memory flush timer. Triggers 60s after boot to flush cold startup memory into ZRAM swap, minimizing idle memory footprint.",
    ),
    "dusky_pro_active_zram_swap.timer": (
        "Proactive ZRAM Swap",
        "Proactive MGLRU slice skimmer timer. Checks memory every 6 minutes and reclaims cold anonymous pages into ZRAM when RAM usage reaches 70%.",
    ),
    "ufw.service": (
        "Firewall (UFW)",
        "Uncomplicated Firewall. A user-friendly front-end for iptables to manage network access rules.",
    ),
    "linux-modules-cleanup.service": (
        "Old Kernel Cleanup",
        "Oneshot boot service provided by kernel-modules-hook. Automatically cleans up orphaned kernel module directories in /usr/lib/modules after a kernel update.",
    ),
    "snapper-cleanup.timer": (
        "Snapper Cleanup Timer",
        "Runs Snapper's snapshot cleanup service every hour.",
    ),
    "fstrim.timer": (
        "Weekly SSD Trim",
        "Discards unused filesystem blocks once a week on supported storage.",
    ),
    "systemd-tmpfiles-clean.timer": (
        "Temporary Directory Cleanup",
        "Daily cleanup of stale files in /tmp and /var/tmp via systemd-tmpfiles --clean. Vendor-enabled through timers.target; static unit, use start/stop.",
    ),
    "dusky_keylogger.service": (
        "Dusky Keystroke Stats",
        "Always-on keystroke statistics daemon. Captures raw key presses via evdev (no Wayland/X11), classifies them (Shift/Caps/NumLock, shortcut chords), and stores them with kernel timestamps in SQLite at ~/.local/share/dusky-keylogger/keys.db (mode 0600). Powers the `dusky stats` / `dusky dashboard` analytics. Stop/disable it here to pause logging.",
    ),
    "dusky_powertop_autotune.timer": (
        "Powertop Auto-Tune",
        "One-shot boot timer that runs `powertop --auto-tune` 2 minutes after boot to flip all power tunables to their Good setting. Enable this timer to auto-tune on every boot; the companion dusky_powertop_autotune.service runs only when triggered. Disabled by default because it can conflict with TLP.",
    ),
    "NetworkManager.service": (
        "NetworkManager",
        "Primary network management daemon. Detects, configures, and maintains Wi-Fi, Ethernet, and mobile broadband connections.",
    ),
    "bluetooth.service": (
        "Bluetooth Daemon (BlueZ)",
        "Linux Bluetooth protocol stack daemon. Manages Bluetooth adapters, pairing, audio streaming, and peripheral connections.",
    ),
    "systemd-timesyncd.service": (
        "Network Time Synchronization",
        "Systemd network time synchronization daemon using SNTP. Synchronizes the local system clock across the network.",
    ),
    "systemd-resolved.service": (
        "Systemd DNS Resolver",
        "Network name resolution service providing local DNS caching, DNSSEC validation, and LLMNR/mDNS hostname resolution.",
    ),
    "udisks2.service": (
        "Storage Daemon (udisks2)",
        "Disk management and storage service. Handles automatic mounting and unmounting of flash drives, external SSDs, and storage partitions.",
    ),
    "thermald.service": (
        "Thermal Daemon (thermald)",
        "Monitors CPU temperature sensors and dynamically applies cooling controls (P-states, T-states, cooling fans) to prevent thermal throttling.",
    ),
    "acpid.service": (
        "ACPI Event Daemon",
        "Dispatches Advanced Configuration and Power Interface hardware events, such as laptop lid close/open, power button presses, and AC adapter plug/unplug.",
    ),
    "snapper-timeline.timer": (
        "Snapper Hourly Timeline",
        "Creates automated hourly Btrfs snapshots of the root and home subvolumes for granular point-in-time rollbacks.",
    ),
    "reflector.timer": (
        "Pacman Mirrorlist Reflector",
        "Weekly system timer that benchmarks available Arch Linux mirrors by download speed and updates /etc/pacman.d/mirrorlist.",
    ),
    "asusd.service": (
        "ASUS ROG/TUF Daemon (asusd)",
        "Hardware control daemon for ASUS laptops. Manages battery charge limits, fan profiles, keyboard RGB lighting, and anime matrix displays.",
    ),
    "supergfxd.service": (
        "SuperGFX dGPU Switcher",
        "Dedicated GPU switching daemon for ASUS and hybrid laptops. Allows switching between Integrated, Hybrid, and Dedicated GPU graphics modes.",
    ),
}

import concurrent.futures


# =============================================================================
# FAST TARGETED CORE FETCH (Tabs 0-1)
# Only queries the curated units instead of enumerating all installed units.
# =============================================================================
def _fetch_core_installed(scope: str, units: list[str]) -> dict[str, str]:
    """Checks only specific units for existence via targeted list-unit-files query."""
    return SystemdEngine.list_unit_files(scope, units)


# Fast path: query only the curated units in two subprocess calls.
_core_user_units = list(CORE_USER_DEFS.keys())
_core_sys_units = list(CORE_SYSTEM_DEFS.keys())

with concurrent.futures.ThreadPoolExecutor(max_workers=2) as _fast_exec:
    _f_core_user = _fast_exec.submit(_fetch_core_installed, "user", _core_user_units)
    _f_core_sys = _fast_exec.submit(_fetch_core_installed, "system", _core_sys_units)

    _core_installed_user = _f_core_user.result()
    _core_installed_sys = _f_core_sys.result()

# The frontend's native menu rows are value-less folders. They never reach the
# systemd engine, and their children keep their real unit keys and scopes.
CORE_USER_SECTIONS = (
    ("Desktop & Session", (
        "hyprsunset.service", "hypridle.service", "osd_lock.service",
        "dusky_polkit.service", "dusky_clipboard.service", "dusky_vdagent.service",
        "dusky_oom_shield.service",
        "wireplumber.service",
    )),
    ("Remote Displays & Streaming", (
        "dusky_vnc_desktop.service", "dusky_vnc_display.service",
        "dusky_moonlight_display.service", "app-dev.lizardbyte.app.Sunshine.service",
    )),
    ("Panels & Integration", (
        "dusky.service", "network_meter.service",
        "dusky_notif_time.service", "dusky_visualizer.service", "dusky_screentime.service",
        "gamemoded.service",
    )),
    ("Media & AI", (
        "dusky_llm.service", "dusky_stt.service",
    )),
    ("Power & Monitoring", (
        "dusky_battery.service", "dusky_ram_monitor.service",
    )),
    ("Storage & Maintenance", (
        "dusky_firefox_cache.service", "dusky_firefox_cache_resync.timer",
        "update_checker.timer",
    )),
    ("Kernel Compilation", (
        "modprobed-db.service", "modprobed-db.timer",
    )),
)

CORE_SYSTEM_SECTIONS = (
    ("Containers", (
        "docker.service",
    )),
    ("Power & Hardware", (
        "tlp.service", "battery-charge-limit.service", "dusky_cpu.service",
        "dusky_kbd_backlight.service", "ghelper-gpu-boot.service",
        "glance_cpu_pkg_watt.service", "thermald.service", "acpid.service",
        "asusd.service", "supergfxd.service", "dusky_zram_recompress.timer",
        "dusky_boot_zram_flush.timer", "dusky_pro_active_zram_swap.timer",
        "dusky_powertop_autotune.timer",
    )),
    ("Input & Session", (
        "numlock_disable.service", "dusky_keylogger.service",
    )),
    ("Network & Security", (
        "NetworkManager.service", "bluetooth.service", "systemd-timesyncd.service",
        "systemd-resolved.service", "ufw.service", "firewalld.service",
        "tailscaled.service", "sshd.service", "warp-svc.service", "vsftpd.service",
    )),
    ("Storage & Maintenance", (
        "udisks2.service", "dusky_snapshot.timer", "snapper-timeline.timer",
        "snapper-cleanup.timer", "fstrim.timer", "systemd-tmpfiles-clean.timer",
        "reflector.timer", "linux-modules-cleanup.service",
    )),
)


def _append_core_sections(tab_idx, definitions, installed, scope, sections, rows=None):
    if rows is None:
        rows = SCHEMA[tab_idx]
    assigned = set()
    for section_idx, (title, units) in enumerate((*sections, ("Other", tuple(definitions)))):
        members = [
            unit for unit in units
            if unit in definitions and unit in installed and unit not in assigned
        ]
        if not members:
            continue
        folder_key = f"__core_{scope}_{section_idx}"
        rows.append(
            ConfigItem(
                label=f"{title} ({len(members)})",
                key=folder_key,
                type_="menu",
                default=None,
                is_parent=True,
                expanded=True,
                extended_help=f"{title}: {len(members)} installed units. Press Enter to expand or collapse.",
            )
        )
        for unit in members:
            label, help_text = definitions[unit]
            rows.append(
                ConfigItem(
                    label=label,
                    key=unit,
                    scope=scope,
                    type_="bool",
                    default=False,
                    parent_ref=folder_key,
                    extended_help=(
                        f"**Unit:** `{unit}`\n**Scope:** {scope.title()}\n"
                        "**Switch:** Startup enablement; enabling also starts and disabling also stops the unit.\n\n"
                        f"{help_text}"
                    ),
                )
            )
        assigned.update(members)


# --- TABS 0-1: CURATED CORE UNITS (instant) ---
_append_core_sections(0, CORE_USER_DEFS, _core_installed_user, "user", CORE_USER_SECTIONS)
_append_core_sections(1, CORE_SYSTEM_DEFS, _core_installed_sys, "system", CORE_SYSTEM_SECTIONS)

# --- TAB 7: PRESETS ---
# Empty – populated at runtime by User Presets via ENABLE_USER_PRESETS /
# USER_PRESETS_TAB="Presets" -> Reset to Defaults / Save as Preset / Import.
# No static presets needed; AI services (dusky_llm/stt) now live in Core User.

# --- TAB 8: READ ONLY ---
# Populated by discovery from non-manageable service and timer states.


# =============================================================================
# DEFERRED FULL FETCH
# The TUI calls DEFERRED_LOAD() after initial render and on F5 refresh.
# =============================================================================
def _fetch_all_unit_files(scope: str) -> tuple[set, set, set, dict[str, str]]:
    """Return service names, enabled names, timer names, and raw unit-file states."""
    installed_srv = set()
    enabled_srv = set()
    installed_tmr = set()
    unit_states = {}

    for unit, state in SystemdEngine.list_unit_files(scope).items():
        if unit.endswith(".service"):
            installed_srv.add(unit)
            unit_states[unit] = state
            if state in ENABLED_STATES:
                enabled_srv.add(unit)
        elif unit.endswith(".timer"):
            installed_tmr.add(unit)
            unit_states[unit] = state

    return installed_srv, enabled_srv, installed_tmr, unit_states


def _fetch_active_services(scope: str) -> set:
    call = SystemdEngine._prefix(scope) + [
        "list-units",
        "--type=service",
        "--state=active",
        "--no-pager",
        "--no-legend",
    ]
    res = SystemdEngine._run(call, 10)
    if res.returncode:
        raise RuntimeError(res.stderr.strip() or f"systemctl exited with status {res.returncode}")
    return {line.split()[0] for line in res.stdout.splitlines() if line.strip()}


def _unit_sort_key(unit: str) -> tuple[str, str]:
    return unit.casefold(), unit


def DEFERRED_LOAD() -> tuple[list[int], dict[int, list[ConfigItem]], dict[str, str]]:
    """
    Populates tabs 2-6 and 8, and refreshes Core from the same inventory.
    Runs full scans after the initial UI render and returns replacement rows.
    Returns populated tab indices, new rows, and the collected unit states.
    Called by the TUI after its initial render of tabs 0-1.
    """
    with concurrent.futures.ThreadPoolExecutor(max_workers=4) as executor:
        user_all = executor.submit(_fetch_all_unit_files, "user")
        sys_all = executor.submit(_fetch_all_unit_files, "system")
        user_active = executor.submit(_fetch_active_services, "user")
        sys_active = executor.submit(_fetch_active_services, "system")
        installed_user_srv, enabled_user, timers_user, user_states = user_all.result()
        installed_sys_srv, enabled_sys, timers_sys, sys_states = sys_all.result()
        active_user_raw = user_active.result()
        active_sys_raw = sys_active.result()

    # Concrete instances are absent from a full list-unit-files scan on
    # systemd 262. Include only active instances that systemd can load.
    for scope, active, installed, enabled, states in (
        ("user", active_user_raw, installed_user_srv, enabled_user, user_states),
        ("system", active_sys_raw, installed_sys_srv, enabled_sys, sys_states),
    ):
        instances = sorted((unit for unit in active if "@" in unit and "@." not in unit), key=_unit_sort_key)
        for unit, unit_state in SystemdEngine.list_unit_files(scope, instances).items():
            installed.add(unit)
            states[unit] = unit_state
            if unit_state in ENABLED_STATES:
                enabled.add(unit)

    installed_user = installed_user_srv | timers_user
    installed_sys = installed_sys_srv | timers_sys

    active_user = active_user_raw.intersection(installed_user_srv)
    active_sys = active_sys_raw.intersection(installed_sys_srv)
    new_schema = {i: [] for i in (*range(7), 8)}
    _append_core_sections(0, CORE_USER_DEFS, user_states, "user", CORE_USER_SECTIONS, new_schema[0])
    _append_core_sections(1, CORE_SYSTEM_DEFS, sys_states, "system", CORE_SYSTEM_SECTIONS, new_schema[1])

    # Track used units (core tabs already populated, avoid duplicates in "All" tabs)
    used_user = CORE_USER_DEFS.keys() & installed_user
    used_sys = CORE_SYSTEM_DEFS.keys() & installed_sys

    # --- TAB 2: ACTIVE SERVICES ---
    for unit in sorted(active_user, key=_unit_sort_key):
        if "@." in unit or user_states[unit] not in MANAGEABLE_STATES:
            continue
        new_schema[2].append(
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

    for unit in sorted(active_sys, key=_unit_sort_key):
        if "@." in unit or sys_states[unit] not in MANAGEABLE_STATES:
            continue
        new_schema[2].append(
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
    for unit in sorted(enabled_user, key=_unit_sort_key):
        if "@." in unit:
            continue
        new_schema[3].append(
            ConfigItem(
                label=unit,
                key=unit,
                scope="user",
                type_="bool",
                default=False,
                group="User Services",
                extended_help=f"**Unit:** `{unit}`\n**Scope:** User\n\nEnabled to start automatically with the user manager.",
            )
        )

    for unit in sorted(enabled_sys, key=_unit_sort_key):
        if "@." in unit:
            continue
        new_schema[3].append(
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
    for unit in sorted(timers_user, key=_unit_sort_key):
        if "@." in unit or user_states[unit] not in MANAGEABLE_STATES:
            continue
        new_schema[4].append(
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

    for unit in sorted(timers_sys, key=_unit_sort_key):
        if "@." in unit or sys_states[unit] not in MANAGEABLE_STATES:
            continue
        new_schema[4].append(
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
    for unit in sorted(installed_user - used_user, key=_unit_sort_key):
        if "@." in unit or not unit.endswith(".service") or user_states[unit] not in MANAGEABLE_STATES:
            continue
        new_schema[5].append(
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
    for unit in sorted(installed_sys - used_sys, key=_unit_sort_key):
        if "@." in unit or not unit.endswith(".service") or sys_states[unit] not in MANAGEABLE_STATES:
            continue
        new_schema[6].append(
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

    # --- TAB 8: READ-ONLY UNIT FILE STATES ---
    for state in sorted({value for states in (user_states, sys_states) for value in states.values()} - MANAGEABLE_STATES):
        for scope, states in (("user", user_states), ("system", sys_states)):
            for unit in sorted(
                (name for name, value in states.items() if value == state and "@." not in name),
                key=_unit_sort_key,
            ):
                new_schema[8].append(
                    ConfigItem(
                        label=f"{scope.title()}: {unit}",
                        key=unit,
                        scope=scope,
                        type_="bool",
                        default=False,
                        read_only=True,
                        group=state.title(),
                        extended_help=(
                            f"**Unit:** `{unit}`\n**Scope:** {scope.title()}\n"
                            f"**Systemd state:** `{state}`\n\n"
                            f"{READ_ONLY_STATE_HELP.get(state, 'Systemd reports this unit-file state; inspect the unit for details.')}"
                        ),
                    )
                )

    state = {f"user/{unit}": "true" if value in ENABLED_STATES else "false" for unit, value in user_states.items()}
    state.update({f"system/{unit}": "true" if value in ENABLED_STATES else "false" for unit, value in sys_states.items()})
    return list(new_schema), new_schema, state
