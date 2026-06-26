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
    "All System"
]

SCHEMA = {i: [] for i in range(len(TABS))}

# --- DETAILED EXTENDED HELP DICTIONARIES ---
CORE_USER_DEFS = {
    "hyprsunset.service": (
        "Night Light (Blue Light Filter)",
        "Manages hyprsunset, a Wayland-native blue light filter. Turning this on will adjust the color temperature of your display to reduce eye strain at night."
    ),
    "battery_notify.service": (
        "Battery Level Notifications",
        "Background daemon that monitors your battery level and sends desktop notifications using libnotify when power is running low."
    ),
    "network_meter.service": (
        "Waybar Network Traffic Monitor",
        "Service to track network traffic. Often used in conjunction with Waybar to display real-time upload and download speeds."
    ),
    "dusky.service": (
        "Dusky Background Service",
        "The primary Dusky ecosystem background service. Handles core daemon tasks required for the environment."
    ),
    "dusky_quickpanal.service": (
        "Dusky quickpanal Service",
        "Manages the Dusky quick access panel (Quickpanal) overlay."
    ),
    "update_checker.timer": (
        "Automatic Update Checker",
        "Periodically checks your package manager for system updates and caches the result for your status bar."
    ),
    "hypridle.service": (
        "Hyprland Idle Daemon",
        "Hyprland's idle management daemon. Handles screen dimming, locking, and DPMS sleep states when you are away from the computer."
    ),
    "osd_lock.service": (
        "OSD for CapsLock,NumLock,ScrollLock",
        "On-Screen Display service for hardware lock keys. Shows a visual pop-up when Caps Lock, Num Lock, or Scroll Lock is toggled."
    ),
    "hyprpolkitagent.service": (
        "(Polkit) Root Password Prompt",
        "The authentication agent for Hyprland. This is what prompts you for a password when an app requests root access (like pkexec)."
    )
}

CORE_SYSTEM_DEFS = {
    "vsftpd.service": (
        "FTP Server (vsftpd)",
        "Very Secure FTP Daemon. Manages the FTP server for file transfers. Only enable this if you actively need to host an FTP server."
    ),
    "tlp.service": (
        "TLP Power Management",
        "Advanced power management for Linux. Applies various battery-saving tweaks to the kernel, PCI, and USB devices."
    ),

    "swayosd-libinput-backend.service": (
        "SwayOSD Input Backend",
        "Backend service for SwayOSD. Handles raw libinput events to render volume/brightness overlays without relying on the window manager."
    ),
    "sshd.service": (
        "SSH Server (OpenSSH)",
        "OpenSSH server daemon. Allows remote access to this machine via SSH. Ensure your firewall is configured if exposing this to the internet."
    ),
    "warp-svc.service": (
        "Cloudflare WARP VPN",
        "Cloudflare WARP daemon. Provides a fast, secure VPN tunnel using WireGuard to route your DNS and internet traffic."
    ),
    "firewalld.service": (
        "Firewall (firewalld)",
        "Dynamic firewall manager. Provides a D-Bus interface to manage firewall rules and network zones."
    ),
    "tailscaled.service": (
        "Tailscaled",
        "Allows remote access"
    ),
    "dusky_snapshot.timer": (
        "3 Day Auto Snapshots (Backup)",
        "Triggers a snapshot automaticaly every 3 days, while automatically cleaning up the oldest snapshot (max 6)."
    ),
    "zram-recompress.timer": (
        "ZRAM 15M Cold Pages Compressor",
        "Auto compresses cold pages in both zram0 and zram1 with zstd level 3 every 15 minutes to reclaim memory"
    ),
    "dusky_boot_mem_reclaim.timer": (
        "1Min Boot Memory Reclaimer",
        "Oneshot boot reclaimer timer. Triggers exactly 1 minute after boot to compress and swap cold initialization memory to ZRAM swap, reducing the startup memory footprint."
    ),

    "ufw.service": (
        "Firewall (UFW)",
        "Uncomplicated Firewall. A user-friendly front-end for iptables to manage network access rules."
    )

}

import concurrent.futures

# --- HIGH SPEED DISCOVERY ROUTINE ---
def _fetch_all_unit_files(scope: str) -> tuple[set, set, set]:
    """Returns (installed_services, enabled_services, installed_timers) in a single pass."""
    call = ["systemctl", "list-unit-files", "--type=service,timer", "--no-pager", "--no-legend"]
    if scope == "user":
        call.insert(1, "--user")
        
    installed_srv = set()
    enabled_srv = set()
    installed_tmr = set()
    
    try:
        res = subprocess.run(call, capture_output=True, text=True, stdin=subprocess.DEVNULL, timeout=3)
        for line in res.stdout.splitlines():
            if not line: continue
            parts = line.split()
            if len(parts) < 2: continue
            unit, state = parts[0], parts[1]
            
            if unit.endswith(".service"):
                installed_srv.add(unit)
                if state == "enabled":
                    enabled_srv.add(unit)
            elif unit.endswith(".timer"):
                installed_tmr.add(unit)
                
        return installed_srv, enabled_srv, installed_tmr
    except Exception:
        return set(), set(), set()

def _fetch_active_services(scope: str) -> set:
    call = ["systemctl", "list-units", "--type=service", "--state=active", "--no-pager", "--no-legend"]
    if scope == "user":
        call.insert(1, "--user")
    try:
        res = subprocess.run(call, capture_output=True, text=True, stdin=subprocess.DEVNULL, timeout=3)
        return {line.split()[0] for line in res.stdout.splitlines() if line}
    except Exception:
        return set()

# 1. Fully Parallelized Subprocess Execution
with concurrent.futures.ThreadPoolExecutor(max_workers=4) as executor:
    f_user_all = executor.submit(_fetch_all_unit_files, "user")
    f_sys_all = executor.submit(_fetch_all_unit_files, "system")
    f_user_act = executor.submit(_fetch_active_services, "user")
    f_sys_act = executor.submit(_fetch_active_services, "system")
    
    installed_user_srv, enabled_user, timers_user = f_user_all.result()
    installed_sys_srv, enabled_sys, timers_sys = f_sys_all.result()
    active_user_raw = f_user_act.result()
    active_sys_raw = f_sys_act.result()

installed_user = installed_user_srv | timers_user
installed_sys = installed_sys_srv | timers_sys

# 2. Compile Active States (Filtered against installed logic)
active_user = active_user_raw.intersection(installed_user_srv)
active_sys = active_sys_raw.intersection(installed_sys_srv)

# Tracking sets to avoid putting core/timer services into the "All" tabs
used_user = set()
used_sys = set()

# --- TAB 0: CORE USER ---
for unit, (label, help_text) in CORE_USER_DEFS.items():
    if unit in installed_user:
        SCHEMA[0].append(ConfigItem(
            label=label, key=unit, scope="user", type_="bool", default=False,
            extended_help=f"**Unit:** `{unit}`\n**Scope:** User\n\n{help_text}"
        ))
        used_user.add(unit)

# --- TAB 1: CORE SYSTEM ---
for unit, (label, help_text) in CORE_SYSTEM_DEFS.items():
    if unit in installed_sys:
        SCHEMA[1].append(ConfigItem(
            label=label, key=unit, scope="system", type_="bool", default=False,
            extended_help=f"**Unit:** `{unit}`\n**Scope:** System\n\n{help_text}"
        ))
        used_sys.add(unit)

# --- TAB 2: ACTIVE SERVICES ---
for unit in sorted(active_user):
    if "@" in unit: continue
    SCHEMA[2].append(ConfigItem(
        label=unit, key=unit, scope="user", type_="bool", default=False, group="User Services",
        extended_help=f"**Unit:** `{unit}`\n**Scope:** User\n\nCurrently active user-level service."
    ))

for unit in sorted(active_sys):
    if "@" in unit: continue
    SCHEMA[2].append(ConfigItem(
        label=unit, key=unit, scope="system", type_="bool", default=False, group="System Services",
        extended_help=f"**Unit:** `{unit}`\n**Scope:** System\n\nCurrently active system-level service."
    ))

# --- TAB 3: ENABLED SERVICES ---
for unit in sorted(enabled_user):
    if "@" in unit: continue
    SCHEMA[3].append(ConfigItem(
        label=unit, key=unit, scope="user", type_="bool", default=False, group="User Services",
        extended_help=f"**Unit:** `{unit}`\n**Scope:** User\n\nEnabled to start automatically on boot."
    ))

for unit in sorted(enabled_sys):
    if "@" in unit: continue
    SCHEMA[3].append(ConfigItem(
        label=unit, key=unit, scope="system", type_="bool", default=False, group="System Services",
        extended_help=f"**Unit:** `{unit}`\n**Scope:** System\n\nEnabled to start automatically on boot."
    ))

# --- TAB 4: TIMERS ---
for unit in sorted(timers_user):
    SCHEMA[4].append(ConfigItem(
        label=unit, key=unit, scope="user", type_="bool", default=False, group="User Timers",
        extended_help=f"**Unit:** `{unit}`\n**Scope:** User\n\nSystemd timer unit (Cron alternative)."
    ))
    used_user.add(unit)

for unit in sorted(timers_sys):
    SCHEMA[4].append(ConfigItem(
        label=unit, key=unit, scope="system", type_="bool", default=False, group="System Timers",
        extended_help=f"**Unit:** `{unit}`\n**Scope:** System\n\nSystemd timer unit (Cron alternative)."
    ))
    used_sys.add(unit)

# --- TAB 5: ALL USER ---
for unit in sorted(installed_user - used_user):
    if "@" in unit or not unit.endswith(".service"): continue
    SCHEMA[5].append(ConfigItem(
        label=unit, key=unit, scope="user", type_="bool", default=False, group=unit[0].upper(),
        extended_help=f"**Unit:** `{unit}`\n**Scope:** User\n\nAuto-discovered service."
    ))

# --- TAB 6: ALL SYSTEM ---
for unit in sorted(installed_sys - used_sys):
    if "@" in unit or not unit.endswith(".service"): continue
    SCHEMA[6].append(ConfigItem(
        label=unit, key=unit, scope="system", type_="bool", default=False, group=unit[0].upper(),
        extended_help=f"**Unit:** `{unit}`\n**Scope:** System\n\nAuto-discovered service."
    ))
