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
    "tlp-pd.service": (
        "TLP Daemon", 
        "Daemon for TLP power management. Should generally be enabled alongside tlp.service."
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
    "ufw.service": (
        "Firewall (UFW)", 
        "Uncomplicated Firewall. A user-friendly front-end for iptables to manage network access rules."
    )
}

# --- HIGH SPEED DISCOVERY ROUTINE ---
def _discover(scope: str, cmd: str, u_type: str = "service,timer", state: str = "") -> set:
    """Fetches systemctl data efficiently without hanging the UI."""
    call = ["systemctl", cmd, f"--type={u_type}", "--no-pager", "--no-legend"]
    if scope == "user":
        call.insert(1, "--user")
    if state:
        call.extend(["--state", state])
    try:
        res = subprocess.run(call, capture_output=True, text=True, stdin=subprocess.DEVNULL, timeout=3)
        return {line.split()[0] for line in res.stdout.splitlines() if line}
    except Exception:
        return set()

# 1. Fetch Master Lists
installed_user = _discover("user", "list-unit-files")
installed_sys = _discover("system", "list-unit-files")

# 2. Fetch specific states (Intersecting with installed prevents weird transient UI artifacts)
active_user = _discover("user", "list-units", u_type="service", state="active").intersection(installed_user)
active_sys = _discover("system", "list-units", u_type="service", state="active").intersection(installed_sys)

enabled_user = _discover("user", "list-unit-files", u_type="service", state="enabled")
enabled_sys = _discover("system", "list-unit-files", u_type="service", state="enabled")

timers_user = _discover("user", "list-unit-files", u_type="timer")
timers_sys = _discover("system", "list-unit-files", u_type="timer")

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
