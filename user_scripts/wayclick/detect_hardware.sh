#!/usr/bin/env bash
# ==============================================================================
# WAYCLICK DEVICE INSPECTOR
# ==============================================================================
# Capability-based input device classifier with WayClick behavior prediction.
# Uses WayClick’s configured filters and reads devices without write access.
# ==============================================================================

set -euo pipefail

# Use the same evdev/udev APIs as WayClick, with its venv preferred.
PYTHON=''
for candidate in "$HOME/contained_apps/uv/wayclick/.venv/bin/python" python python3.15 python3; do
    if command -v "$candidate" >/dev/null && "$candidate" -c \
        'import sys, evdev, pyudev; sys.exit(sys.version_info < (3,15,0,"candidate",3))' &>/dev/null; then
        PYTHON=$candidate
        break
    fi
done
if [[ -z $PYTHON ]]; then
    printf '[ERROR] Python 3.15 with evdev and pyudev is required.\nRun: bash %q --setup\n' \
        "${BASH_SOURCE[0]%/*}/dusky_wayclick.sh" >&2
    exit 1
fi

exec "$PYTHON" -B - "${BASH_SOURCE[0]%/*}/dusky_wayclick.sh" << 'PYTHON_EOF'
import evdev
import os
lazy import pwd
import shlex
import sys
from importlib.metadata import version
from pathlib import Path

import pyudev

# ── ANSI ──
GREEN  = "\033[1;32m"
YELLOW = "\033[1;33m"
CYAN   = "\033[1;36m"
RED    = "\033[1;31m"
BLUE   = "\033[1;34m"
DIM    = "\033[2m"
BOLD   = "\033[1m"
RESET  = "\033[0m"

# ── Linux Input Constants ──
EV_KEY = 1
EV_REL = 2
EV_ABS = 3

REL_X  = 0
REL_Y  = 1

ABS_MT_POSITION_X = 0x35   # 53  — multitouch X axis (touchpads, touchscreens)

BTN_LEFT        = 0x110    # 272 — primary mouse/touchpad button
BTN_GAMEPAD     = 0x130    # 304 — start of gamepad button range
BTN_TOOL_FINGER = 0x145    # 325 — "finger on surface" (touchpads)

# Standard keyboard keycodes: KEY_ESC(1) through KEY_KPDOT(83) + F11/F12
# Any device reporting 20+ of these is a real keyboard.
KEYBOARD_KEYS = set(range(1, 84)) | {87, 88}

# Read literal user settings without executing the toggle launcher.
def load_settings(path: Path) -> dict[str, str | tuple[str, ...]]:
    settings = {}
    wanted = {"ENABLE_TRACKPAD_SOUNDS", "AUTO_DETECT_TRACKPADS", "EXCLUDED_KEYWORDS"}
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line.startswith(("#", "readonly ")) and "<<" in line:
            break
        if not line.startswith("readonly "):
            continue
        key, separator, raw = line.removeprefix("readonly ").partition("=")
        if not separator or key not in wanted:
            continue
        if key == "EXCLUDED_KEYWORDS":
            raw = raw.strip()
            if not raw.startswith("(") or ")" not in raw:
                raise ValueError("EXCLUDED_KEYWORDS must be a literal Bash array")
            words = shlex.split(raw[1:raw.rindex(")")], comments=True)
            settings[key] = tuple(word.strip().casefold() for word in ",".join(words).split(",") if word.strip())
        else:
            words = shlex.split(raw, comments=True)
            if len(words) != 1 or words[0] not in ("true", "false"):
                raise ValueError(f"{key} must be true or false")
            settings[key] = words[0]
    if settings.keys() != wanted:
        raise ValueError("WayClick device settings are missing")
    return settings


try:
    SETTINGS = load_settings(Path(sys.argv[1]))
except (OSError, ValueError) as exc:
    sys.exit(f"[CONFIG ERROR] {exc}")
ENABLE_TRACKPADS = SETTINGS["ENABLE_TRACKPAD_SOUNDS"] == "true"
AUTO_DETECT = SETTINGS["AUTO_DETECT_TRACKPADS"] == "true"
WC_KEYWORDS = SETTINGS["EXCLUDED_KEYWORDS"]
UDEV = pyudev.Context()


def classify_touchpad(dev, keys, abss) -> tuple[bool, str]:
    try:
        props = pyudev.Devices.from_device_file(UDEV, dev.path).properties
        if props.get("ID_INPUT_TOUCHPAD") == "1":
            return True, "udev"
        if props.get("ID_INPUT_TOUCHSCREEN") == "1":
            return False, ""
    except (OSError, pyudev.DeviceNotFoundError):
        pass
    return ((True, "capability") if ABS_MT_POSITION_X in abss and BTN_TOOL_FINGER in keys
            else (False, ""))


# ── Classification ──
def classify(dev):
    caps = dev.capabilities(absinfo=False)
    keys = set(caps.get(EV_KEY, []))
    rels = set(caps.get(EV_REL, []))
    abss = set(caps.get(EV_ABS, []))

    is_touchpad, touchpad_source = classify_touchpad(dev, keys, abss)
    is_mouse    = REL_X in rels and REL_Y in rels and not is_touchpad
    is_keyboard = len(keys & KEYBOARD_KEYS) >= 20
    is_gamepad  = any(c in keys for c in range(BTN_GAMEPAD, BTN_GAMEPAD + 16))
    has_ev_key  = EV_KEY in caps

    # --- Type string (multi-function devices get combined labels) ---
    parts = []
    if is_touchpad: parts.append("TOUCHPAD")
    if is_keyboard: parts.append("KEYBOARD")
    if is_mouse:    parts.append("MOUSE")
    if is_gamepad:  parts.append("GAMEPAD")

    if not parts:
        if BTN_LEFT in keys:  parts.append("BUTTONS")
        elif keys:            parts.append("SYSTEM")
        elif abss:            parts.append("SENSOR")
        else:                 parts.append("OTHER")

    dev_type = " + ".join(parts)

    # --- Color (priority: touchpad > keyboard > mouse > dim) ---
    if   is_touchpad: color = YELLOW
    elif is_keyboard: color = GREEN
    elif is_mouse:    color = CYAN
    else:             color = DIM

    # Match the launcher's configured filters; audio mappings still decide
    # which monitored button/key codes actually produce a sound.
    name_lower = (dev.name or "<unnamed>").casefold()
    matched_kw = next((k for k in WC_KEYWORDS if k in name_lower), None)
    if not has_ev_key:
        wc_cat = "ignored"
        wc_display = f"{DIM}— no EV_KEY{RESET}"
    elif not ENABLE_TRACKPADS and matched_kw:
        wc_cat = "filtered"
        wc_display = f'{YELLOW}✗ keyword: "{matched_kw}"{RESET}'
    elif not ENABLE_TRACKPADS and AUTO_DETECT and is_touchpad:
        wc_cat = "filtered"
        wc_display = f"{YELLOW}✗ {touchpad_source} touchpad{RESET}"
    else:
        wc_cat = "active"
        wc_display = f"{GREEN}▶ monitored{RESET}"

    return dev_type, color, wc_display, wc_cat

# Snapshot and close each device immediately; a hot-unplug must not abort the table.
devices = []
for path in evdev.list_devices(writable=False):
    dev = None
    try:
        dev = evdev.InputDevice(path, readonly=True)
        result = classify(dev)
        devices.append((dev.path, dev.name or "<unnamed>", result))
    except OSError:
        continue
    finally:
        if dev is not None:
            dev.close()

if not devices:
    user = pwd.getpwuid(os.getuid()).pw_name
    print(f"\n{RED}No readable input devices found.{RESET}")
    print("Check connected devices and this session’s input-device permissions.")
    print(f"If input group access is missing: {CYAN}sudo usermod -aG input {shlex.quote(user)}{RESET}")
    print("Then log out and log back in.")
    sys.exit(1)

devices.sort(key=lambda row: int(Path(row[0]).name.removeprefix("event")))

# ── Column Widths ──
W_NAME = 42
W_PATH = 8
W_TYPE = 20
LINE_W = 95

# ── Header ──
py_ver = sys.version.split()[0]
ev_ver = version("evdev")

print(f"\n{BLUE}{'─' * LINE_W}{RESET}")
print(f" {BOLD}WAYCLICK DEVICE INSPECTOR{RESET}                         "
      f"{DIM}Python {py_ver} │ evdev {ev_ver}{RESET}")
print(f"{BLUE}{'─' * LINE_W}{RESET}")
print(f" {BOLD}{'DEVICE':<{W_NAME}}{RESET} │ {BOLD}{'PATH':<{W_PATH}}{RESET} "
      f"│ {BOLD}{'TYPE':<{W_TYPE}}{RESET} │ {BOLD}WAYCLICK{RESET}")
print(f"{BLUE}{'─' * LINE_W}{RESET}")

# ── Table Body ──
type_counts = {}
wc_counts = {"active": 0, "filtered": 0, "ignored": 0}

for path, name, result in devices:
    dev_type, color, wc_display, wc_cat = result
    event_name = os.path.basename(path)
    name = name[:W_NAME]

    print(f" {color}{name:<{W_NAME}}{RESET} │ {event_name:<{W_PATH}} "
          f"│ {color}{dev_type:<{W_TYPE}}{RESET} │ {wc_display}")

    primary = dev_type.split(" + ")[0]
    type_counts[primary] = type_counts.get(primary, 0) + 1
    wc_counts[wc_cat] += 1


# ── Summary ──
print(f"{BLUE}{'─' * LINE_W}{RESET}")

type_str = " \u2502 ".join(f"{t}: {c}" for t, c in sorted(type_counts.items()))
print(f" {DIM}{type_str}{RESET}")

a, f, i = wc_counts["active"], wc_counts["filtered"], wc_counts["ignored"]
print(f" {DIM}Configured WayClick: {a} monitored, {f} filtered, {i} ignored "
      f"({len(devices)} total){RESET}")
print()
PYTHON_EOF
