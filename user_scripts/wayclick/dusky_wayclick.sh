#!/usr/bin/env bash
# WayClick — Bash 5.3 / Python 3.15+ / PipeWire / evdev.
# One maintained launcher; the embedded runner is generated during setup/startup.
set -euo pipefail
shopt -s inherit_errexit nullglob

RUN_MODE=run
(( $# <= 1 )) || { printf 'Usage: %s [--setup|--reset]\n' "${0##*/}" >&2; exit 1; }
case ${1-} in
    --setup) RUN_MODE=setup ;;
    --reset) RUN_MODE=reset ;;
    --help|-h)
        printf 'Usage: %s [--setup|--reset]\n  No arguments: toggle WayClick\n  --setup: prepare the environment\n  --reset: stop and remove the environment\n' "${0##*/}"
        exit 0 ;;
    '') ;;
    *) printf 'Unknown option: %s\n' "$1" >&2; exit 1 ;;
esac

# USER CONFIGURATION — also edited by dusky_tui_wayclick.sh.
# Audio pack: subfolder name inside ~/.config/wayclick/ containing .wav files.
# Example:  ~/.config/wayclick/audio_pack_1/click.wav
readonly AUDIO_PACK="audio_pack_1"

# SDL audio buffer size (in samples). Lower = less latency, but may crackle.
# If you hear pops/crackles, raise this value one step.
#   128  → ~2.7ms   (ultra-low latency, modern hardware)
#   256  → ~5.3ms   (balanced)
#   512  → ~10.7ms  (safe fallback)
readonly AUDIO_BUFFER_SIZE="128"

# Audio sample rate (Hz). Match to your .wav files for best results.
#   44100 → CD quality
#   48000 → Standard (recommended, matches PipeWire default)
readonly AUDIO_SAMPLE_RATE="48000"

# Maximum simultaneous sound channels. 16 covers fast typing.
# Raise to 32 if sounds cut off during rapid bursts.
readonly AUDIO_MIX_CHANNELS="16"

# Trackpad/touchpad sounds:
#   "true"  → Trackpads WILL play sounds (no filtering applied)
#   "false" → Trackpads will be detected and excluded (default)
readonly ENABLE_TRACKPAD_SOUNDS="false"

# Auto-detect touchpads using udev tags, then multitouch + finger capabilities.
# Only active when ENABLE_TRACKPAD_SOUNDS is "false".
# Set "false" to rely ONLY on the keyword blacklist below.
readonly AUTO_DETECT_TRACKPADS="true"

# Manual keyword blacklist (case-insensitive substrings matched against device names).
# Devices matching ANY keyword are excluded.
# Only active when ENABLE_TRACKPAD_SOUNDS is "false".
# Tip: You can add non-trackpad keywords too (e.g. "mouse" to silence mouse clicks).
readonly EXCLUDED_KEYWORDS=("touchpad" "trackpad" "glidepoint" "magic trackpad" "clickpad")

# How often to scan for newly connected devices (seconds).
# 1.0 is recommended. Going below 0.5 wastes CPU for negligible benefit.
readonly HOTPLUG_POLL_SECONDS="1.0"

# Set "true" to print per-keypress latency measurements to the terminal.
readonly DEBUG_MODE="false"

script_path=${BASH_SOURCE[0]}
[[ $script_path == /* ]] || script_path="$PWD/$script_path"
[[ ! -L $script_path ]] || script_path=$(readlink -f -- "$script_path")
readonly SCRIPT_PATH="$script_path"
readonly SOUND_INSTALLER="${SCRIPT_PATH%/*}/sounds/wayclick_soundpacks_download.sh"
readonly BASE_DIR="$HOME/contained_apps/uv/wayclick"
readonly VENV_DIR="$BASE_DIR/.venv"
readonly PYTHON_BIN="$VENV_DIR/bin/python"
readonly RUNNER_SCRIPT="$BASE_DIR/runner.py"
readonly CONFIG_DIR="$HOME/.config/wayclick"
readonly STATE_FILE="$HOME/.config/dusky/settings/wayclick"
readonly PID_FILE="$BASE_DIR/wayclick.pid"
readonly READY_FILE="$BASE_DIR/wayclick.ready.$BASHPID"
readonly MARKER_FILE="$BASE_DIR/.build_marker_v12"
CHILD_PID='' CHILD_START='' LOCK_FD='' RUNNER_TMP=''
LOCK_HELD=false

fail() {
    printf '[ERROR] %s\n' "$*" >&2
    notify_user "$*"
    exit 1
}
notify_user() {
    if command -v notify-send >/dev/null; then
        (
            # Notifications must not hold the operation lock or delay toggles.
            if $LOCK_HELD; then exec {LOCK_FD}>&-; fi
            exec timeout --kill-after=1 3 notify-send -t 2000 --app-name=WayClick WayClick "$1"
        ) >/dev/null 2>&1 &
    fi
}
update_state() {
    local tmp="${STATE_FILE}.tmp.$BASHPID"
    mkdir -p "${STATE_FILE%/*}"
    printf '%s\n' "$1" > "$tmp"
    mv -f -- "$tmp" "$STATE_FILE"
}
acquire_lock() {
    if ! $LOCK_HELD; then
        exec {LOCK_FD}> "$BASE_DIR/wayclick.lock"
        flock -w 30 "$LOCK_FD" || fail 'Another WayClick operation is still running.'
        LOCK_HELD=true
    fi
}
release_lock() {
    if $LOCK_HELD; then
        flock -u "$LOCK_FD"
        exec {LOCK_FD}>&-
        LOCK_HELD=false
    fi
}
# /proc field 22 follows the final ') ' in stat; comm may itself contain ')'.
proc_identity() {
    local stat
    local -a fields
    IFS= read -r stat 2>/dev/null < "/proc/$1/stat" || return 1
    read -ra fields <<< "${stat##*) }"
    (( ${#fields[@]} >= 20 )) || return 1
    [[ ${fields[0]} != Z && ${fields[0]} != X ]] || return 1
    REPLY=${fields[19]}
}
runner_alive() {
    local pid=$1 start=$2 arg found=false
    [[ $pid =~ ^[1-9][0-9]*$ && $start =~ ^[0-9]+$ ]] || return 1
    [[ -O /proc/$pid ]] || return 1
    proc_identity "$pid" && [[ $REPLY == "$start" ]] || return 1
    [[ -r /proc/$pid/cmdline ]] || return 1
    while IFS= read -r -d '' arg; do
        [[ $arg == "$RUNNER_SCRIPT" ]] && found=true
    done 2>/dev/null < "/proc/$pid/cmdline"
    $found
}
find_runners() {
    local pid start
    if [[ -r $PID_FILE ]] && read -r pid start < "$PID_FILE" && runner_alive "$pid" "$start"; then
        printf '%s %s\n' "$pid" "$start"
        return
    fi
    # Migration/recovery only: find older runners without a PID record.
    # pgrep is just a candidate filter; verify an exact argv and process identity.
    while read -r pid; do
        proc_identity "$pid" || continue
        start=$REPLY
        runner_alive "$pid" "$start" && printf '%s %s\n' "$pid" "$start"
    done < <(pgrep -u "$EUID" -f '[/]wayclick/runner[.]py' || true)
    return 0
}
stop_runner() {
    local pid=$1 start=$2 i
    runner_alive "$pid" "$start" || return 0
    kill -TERM "$pid" 2>/dev/null || true
    for (( i=0; i<60; i++ )); do
        runner_alive "$pid" "$start" || return 0
        sleep 0.05
    done
    runner_alive "$pid" "$start" && kill -KILL "$pid" 2>/dev/null || true
    for (( i=0; i<20; i++ )); do
        runner_alive "$pid" "$start" || return 0
        sleep 0.05
    done
    fail "Runner $pid did not stop; environment retained."
}
cleanup() {
    local rc=$? pid start
    trap - EXIT INT TERM HUP
    if [[ -n $CHILD_PID ]]; then
        if [[ -z $CHILD_START ]]; then
            kill -TERM "$CHILD_PID" 2>/dev/null || true
        elif proc_identity "$CHILD_PID" && [[ $REPLY == "$CHILD_START" ]]; then
            # Include the pre-exec window without signaling a reused PID.
            kill -TERM "$CHILD_PID" 2>/dev/null || true
            stop_runner "$CHILD_PID" "$CHILD_START"
        fi
        wait "$CHILD_PID" 2>/dev/null || true
    fi
    rm -f -- "$READY_FILE" ${RUNNER_TMP:+"$RUNNER_TMP"}
    # Serialize cleanup with the next toggle. An old supervisor cannot erase
    # the PID or state of a newer runner.
    acquire_lock
    if [[ -n $CHILD_PID && -r $PID_FILE ]] && read -r pid start < "$PID_FILE" \
        && [[ $pid == "$CHILD_PID" && $start == "$CHILD_START" ]]; then
        rm -f -- "$PID_FILE"
    fi
    if [[ -z $(find_runners) ]]; then
        rm -f -- "$PID_FILE"
        update_state False
    fi
    release_lock
    exit "$rc"
}
(( EUID != 0 )) || fail 'Run WayClick as the desktop user.'
command -v flock >/dev/null || fail 'flock (util-linux) is required.'
mkdir -p "$BASE_DIR"
acquire_lock
trap cleanup EXIT
trap 'exit 130' INT
trap 'exit 143' TERM
trap 'exit 129' HUP

mapfile -t records < <(find_runners)
if (( ${#records[@]} )); then
    [[ $RUN_MODE != setup ]] || fail 'Stop WayClick before rebuilding its environment.'
    for record in "${records[@]}"; do
        read -r pid start <<< "$record"
        stop_runner "$pid" "$start"
    done
    rm -f -- "$PID_FILE"
    update_state False
    notify_user Disabled
    [[ $RUN_MODE == reset ]] || exit 0
fi
if [[ $RUN_MODE == reset ]]; then
    rm -rf -- "$VENV_DIR"
    rm -f -- "$RUNNER_SCRIPT" "$PID_FILE" "$BASE_DIR"/.build_marker_* "$BASE_DIR"/wayclick.ready.*
    printf '[RESET] Environment removed.\n'
    exit 0
fi

# Only first-run setup and explicit repairs query the package database.
# Arch's existing sync database/cache is used; never do a partial -Sy upgrade.
select_python() {
    local candidate candidate_path
    native_python=''
    for candidate in python python3.15; do
        if candidate_path=$(command -v "$candidate") && "$candidate_path" -c \
            'import sys; sys.exit(sys.version_info < (3,15,0,"candidate",3))' 2>/dev/null; then
            native_python=$candidate_path
            return 0
        fi
    done
    return 1
}
install_system_deps() {
    local dep
    local -a deps=(uv gcc linux-api-headers pipewire pipewire-audio
                   pipewire-pulse wireplumber systemd-libs libnotify shadow)
    local -a missing=()
    local -A installed=()
    [[ -r $CONFIG_DIR/$AUDIO_PACK/config.json ]] || deps+=(curl unzip)
    select_python || deps+=(python)
    command -v pacman >/dev/null || fail 'WayClick setup requires Arch Linux/pacman.'
    while IFS= read -r dep; do
        [[ -n $dep ]] && installed["$dep"]=1
    done < <(pacman -Qq -- "${deps[@]}" 2>/dev/null || true)
    for dep in "${deps[@]}"; do
        [[ -v installed[$dep] ]] || missing+=("$dep")
    done
    if (( ${#missing[@]} )); then
        printf '[SETUP] Installing system dependencies: %s\n' "${missing[*]}"
        sudo pacman -S --needed --noconfirm -- "${missing[@]}" \
            || fail 'System dependency installation failed; run setup again after resolving the pacman error.'
    fi
    select_python || fail 'Python 3.15.0rc3 or newer is required from the system installation.'
}
runtime_ready() {
    [[ -x $PYTHON_BIN ]] || return 1
    PYGAME_HIDE_SUPPORT_PROMPT=1 "$PYTHON_BIN" - <<'CHECK' >/dev/null 2>&1
import sys
if sys.version_info < (3, 15, 0, 'candidate', 3):
    sys.exit(1)
import evdev, pygame, pyudev
pyudev.Context()
CHECK
}
prepare_environment() {
    # Recreate corrupt/obsolete environments; reinstalling over broken package
    # metadata can otherwise leave uv satisfied while imports still fail.
    if [[ -e $VENV_DIR || -L $VENV_DIR ]] && ! runtime_ready; then
        rm -rf -- "$VENV_DIR"
        rm -f -- "$MARKER_FILE"
    fi
    if [[ ! -x $PYTHON_BIN ]]; then
        uv venv --python "$native_python" --no-python-downloads "$VENV_DIR"
    fi
    if [[ ! -f $MARKER_FILE ]]; then
        # Portable cached extensions; PyGame's wheels supply their SDL libraries.
        uv pip install --python "$PYTHON_BIN" --only-binary pygame-ce \
            --link-mode=copy --compile-bytecode 'evdev>=2.0' 'pygame-ce>=2.5.8' 'pyudev>=0.24.5'
        runtime_ready || fail 'Python runtime validation failed after setup.'
    fi
}
prepare_soundpack() {
    [[ ! -r $CONFIG_DIR/$AUDIO_PACK/config.json ]] || return 0
    [[ -r $SOUND_INSTALLER ]] || fail "Missing soundpack installer: $SOUND_INSTALLER"
    bash "$SOUND_INSTALLER" --auto --pack "$AUDIO_PACK" \
        || fail "Could not install soundpack '$AUDIO_PACK'."
    [[ -r $CONFIG_DIR/$AUDIO_PACK/config.json ]] || fail "Soundpack '$AUDIO_PACK' has no readable config.json."
}
current_input_access() {
    # One readable virtual device does not imply access to physical keyboards.
    [[ " $(id -nG) " == *' input '* ]]
}
prepare_input_access() {
    current_input_access && return 0
    local setup_user
    setup_user=$(id -un)
    if [[ " $(id -nG "$setup_user") " != *' input '* ]]; then
        printf '[SETUP] Enabling input access for %s.\n' "$setup_user"
        sudo usermod -aG input "$setup_user" || fail 'Could not enable input-device access.'
    fi
    if [[ $RUN_MODE == setup ]]; then
        printf '[SETUP] Log out/in to apply the input group to future desktop sessions.\n'
    else
        # sudo initializes the same user's updated supplementary groups.
        # Run as that user, keeping the desktop bus and Wayland/audio session.
        release_lock
        trap - EXIT INT TERM HUP
        exec sudo --preserve-env=XDG_RUNTIME_DIR,DBUS_SESSION_BUS_ADDRESS,WAYLAND_DISPLAY,XDG_SESSION_TYPE \
            -u "$setup_user" -- bash "$SCRIPT_PATH"
    fi
}
restart_for_setup() {
    [[ ${WC_REPAIR_ATTEMPT:-0} != 1 ]] || fail 'Automatic repair failed; run --setup in a terminal for details.'
    printf '[REPAIR] Rebuilding the environment after a startup failure.\n'
    rm -f -- "$MARKER_FILE" "$PID_FILE" "$READY_FILE"
    CHILD_PID='' CHILD_START=''
    update_state False
    release_lock
    trap - EXIT INT TERM HUP
    exec env WC_REPAIR_ATTEMPT=1 bash "$SCRIPT_PATH"
}

if [[ $RUN_MODE == setup || ! -f $MARKER_FILE || ! -x $PYTHON_BIN || ! -r $CONFIG_DIR/$AUDIO_PACK/config.json ]]; then
    if [[ $RUN_MODE == run && ! -t 0 ]]; then
        # Hyprland's first click opens setup in the preferred Wayland terminal.
        command -v xdg-terminal-exec >/dev/null || fail 'Run WayClick in a terminal once to install dependencies.'
        release_lock
        trap - EXIT INT TERM HUP
        exec xdg-terminal-exec -- bash "$SCRIPT_PATH"
    fi
    install_system_deps
    prepare_environment
    prepare_soundpack
    touch "$MARKER_FILE"
    prepare_input_access
fi

RUNNER_TMP="$BASE_DIR/.runner.$BASHPID.tmp"
cat > "$RUNNER_TMP" << 'PYTHON_EOF'
import asyncio
import json
import math
import os
lazy import random
lazy import time
import signal
import sys
from pathlib import Path

try:
    import uvloop
except ImportError:
    uvloop = None

os.environ["PYGAME_HIDE_SUPPORT_PROMPT"] = "1"
os.environ.setdefault("SDL_AUDIODRIVER", "pipewire,pulseaudio,alsa")
os.environ["SDL_APP_NAME"] = "WayClick"
os.environ["PULSE_PROP"] = "application.name=\"WayClick\""

import evdev
lazy import pyudev
import pygame

sys.stdout.reconfigure(line_buffering=True)
sys.stderr.reconfigure(line_buffering=True)

C_GREEN  = "\033[1;32m"
C_YELLOW = "\033[1;33m"
C_BLUE   = "\033[1;34m"
C_RED    = "\033[1;31m"
C_DIM    = "\033[2m"
C_RESET  = "\033[0m"

if len(sys.argv) != 3:
    sys.exit(f"{C_RED}[USAGE ERROR]{C_RESET} runner.py <config_dir> <pack_name>")

CONFIG_DIR = Path(sys.argv[1]).expanduser()
PACK_NAME = sys.argv[2]
ASSET_DIR = CONFIG_DIR / PACK_NAME
CONFIG_FILE = ASSET_DIR / "config.json"
READY_FILE = os.environ.get("WC_READY_FILE", "").strip()

def env_bool(name: str, default: str) -> bool:
    return os.environ.get(name, default).strip().casefold() == "true"

def env_int(name: str, default: str, minimum: int) -> int:
    raw = os.environ.get(name, default).strip()
    try:
        value = int(raw)
    except ValueError:
        sys.exit(f"{C_RED}[ENV ERROR]{C_RESET} {name} must be an integer, got: {raw!r}")
    if value < minimum:
        sys.exit(f"{C_RED}[ENV ERROR]{C_RESET} {name} must be >= {minimum}, got: {value}")
    return value

def env_float(name: str, default: str, minimum_exclusive: float) -> float:
    raw = os.environ.get(name, default).strip()
    try:
        value = float(raw)
    except ValueError:
        sys.exit(f"{C_RED}[ENV ERROR]{C_RESET} {name} must be a number, got: {raw!r}")
    if not math.isfinite(value) or value <= minimum_exclusive:
        sys.exit(f"{C_RED}[ENV ERROR]{C_RESET} {name} must be > {minimum_exclusive}, got: {value}")
    return value

ENABLE_TRACKPADS = env_bool("ENABLE_TRACKPADS", "false")
AUTO_DETECT = env_bool("WC_AUTO_DETECT", "true")
DEBUG = env_bool("WC_DEBUG", "false")
BUFFER_SIZE = env_int("WC_AUDIO_BUFFER", "512", 16)
SAMPLE_RATE = env_int("WC_AUDIO_RATE", "48000", 8000)
MIX_CHANNELS = env_int("WC_MIX_CHANNELS", "16", 1)
POLL_INTERVAL = env_float("WC_POLL_INTERVAL", "1.0", 0.0)

raw_keywords = os.environ.get("WC_EXCLUDED_KEYWORDS", "touchpad,trackpad")
EXCLUDED_KEYWORDS = tuple(
    keyword.strip().casefold()
    for keyword in raw_keywords.split(",")
    if keyword.strip()
)

_EV_KEY = 1
_EV_ABS = 3
_ABS_MT_POSITION_X = 0x35
_BTN_TOOL_FINGER = 0x145

def mark_ready(path_str: str) -> None:
    if not path_str:
        return
    ready_path = Path(path_str).expanduser()
    ready_path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = ready_path.with_name(f".{ready_path.name}.{os.getpid()}.tmp")
    tmp_path.write_text("ready\n", encoding="utf-8")
    os.replace(tmp_path, ready_path)

try:
    pygame.mixer.init(frequency=SAMPLE_RATE, size=-16, channels=2, buffer=BUFFER_SIZE)
    pygame.mixer.set_num_channels(MIX_CHANNELS)
except pygame.error as exc:
    sys.exit(f"{C_RED}[AUDIO ERROR]{C_RESET} {exc}")

actual_rate = pygame.mixer.get_init()[0]
latency_ms = BUFFER_SIZE / actual_rate * 1000.0
print(
    f"{C_BLUE}[AUDIO]{C_RESET} Requested buffer={BUFFER_SIZE} samples (~{latency_ms:.1f}ms) | "
    f"Rate={actual_rate}Hz | Mix channels={MIX_CHANNELS}"
)

print(f"{C_BLUE}[INFO]{C_RESET}  Config: {CONFIG_FILE}")
print(f"{C_BLUE}[INFO]{C_RESET}  Pack:   {ASSET_DIR}")

try:
    with CONFIG_FILE.open("r", encoding="utf-8") as fh:
        config_data = json.load(fh)
except (OSError, ValueError) as exc:
    sys.exit(f"{C_RED}[CONFIG ERROR]{C_RESET} Failed to load {CONFIG_FILE}: {exc}")

if not isinstance(config_data, dict):
    sys.exit(f"{C_RED}[CONFIG ERROR]{C_RESET} config.json must contain a JSON object.")

mappings_obj = config_data.get("mappings", {})
defaults_obj = config_data.get("defaults", [])

if not isinstance(mappings_obj, dict):
    sys.exit(f"{C_RED}[CONFIG ERROR]{C_RESET} 'mappings' must be an object.")
if not isinstance(defaults_obj, list):
    sys.exit(f"{C_RED}[CONFIG ERROR]{C_RESET} 'defaults' must be an array.")

RAW_KEY_MAP: dict[int, str] = {}
for key, value in mappings_obj.items():
    try:
        keycode = int(key)
    except (TypeError, ValueError):
        sys.exit(f"{C_RED}[CONFIG ERROR]{C_RESET} Invalid keycode in 'mappings': {key!r}")

    if keycode < 0:
        sys.exit(f"{C_RED}[CONFIG ERROR]{C_RESET} Keycodes must be >= 0, got: {keycode}")

    if not isinstance(value, str) or not value.strip():
        sys.exit(f"{C_RED}[CONFIG ERROR]{C_RESET} Invalid sound filename for keycode {keycode}: {value!r}")

    RAW_KEY_MAP[keycode] = value.strip()

DEFAULTS: list[str] = []
for value in defaults_obj:
    if not isinstance(value, str) or not value.strip():
        sys.exit(f"{C_RED}[CONFIG ERROR]{C_RESET} Invalid entry in 'defaults': {value!r}")
    DEFAULTS.append(value.strip())

SOUND_FILES = set(RAW_KEY_MAP.values()) | set(DEFAULTS)
SOUNDS: dict[str, pygame.mixer.Sound] = {}

for filename in SOUND_FILES:
    path = ASSET_DIR / filename
    if path.is_file():
        try:
            sound = pygame.mixer.Sound(str(path))
            sound.set_volume(1.0)
            SOUNDS[filename] = sound
        except pygame.error as exc:
            print(f"{C_YELLOW}[WARN]{C_RESET} Failed to load wav '{filename}': {exc}")
    else:
        print(f"{C_YELLOW}[WARN]{C_RESET} File not found in pack: {filename}")

if not SOUNDS:
    sys.exit(
        f"{C_RED}[AUDIO ERROR]{C_RESET} No sounds loaded. "
        f"Check config.json mappings and .wav files in '{PACK_NAME}'."
    )

print(f"{C_BLUE}[INFO]{C_RESET}  Loaded {len(SOUNDS)} sound(s) from pack '{PACK_NAME}'")

DENSE_CACHE_LIMIT = min(2048, max(1024, max(RAW_KEY_MAP, default=0) + 1))
SOUND_CACHE: list[pygame.mixer.Sound | None] = [None] * DENSE_CACHE_LIMIT
SPARSE_CACHE: dict[int, pygame.mixer.Sound] = {}
DEFAULT_SOUND_OBJS = tuple(SOUNDS[name] for name in DEFAULTS if name in SOUNDS)

for code, filename in RAW_KEY_MAP.items():
    sound = SOUNDS.get(filename)
    if sound is None:
        continue
    if code < DENSE_CACHE_LIMIT:
        SOUND_CACHE[code] = sound
    else:
        SPARSE_CACHE[code] = sound

_dense_cache = SOUND_CACHE
_dense_limit = DENSE_CACHE_LIMIT
_sparse_get = SPARSE_CACHE.get
_defaults = DEFAULT_SOUND_OBJS
_default_count = len(DEFAULT_SOUND_OBJS)
_has_defaults = _default_count > 0
_single_default = DEFAULT_SOUND_OBJS[0] if _default_count == 1 else None
# Warm random before readiness only when random defaults are actually used.
_randrange = random.randrange if _default_count > 1 else None

def play_sound(code: int) -> None:
    sound = _dense_cache[code] if 0 <= code < _dense_limit else _sparse_get(code)
    if sound is not None:
        sound.play()
    elif _single_default is not None:
        _single_default.play()
    elif _has_defaults:
        _defaults[_randrange(_default_count)].play()

if DEBUG:
    _perf = time.perf_counter_ns
    _play_sound = play_sound

    def play_sound(code: int) -> None:
        t0 = _perf()
        _play_sound(code)
        elapsed_us = (_perf() - t0) / 1000.0
        print(f"  ⏱ {elapsed_us:.1f}µs [code={code}]")

# Query libudev in-process, once per newly opened device (no udevadm forks).
_UDEV = pyudev.Context() if AUTO_DETECT and not ENABLE_TRACKPADS else None

def classify_touchpad(path: str, caps: dict[int, list[int]]) -> tuple[bool, str]:
    try:
        props = pyudev.Devices.from_device_file(_UDEV, path).properties
        if props.get("ID_INPUT_TOUCHPAD") == "1":
            return True, "udev"
        if props.get("ID_INPUT_TOUCHSCREEN") == "1":
            return False, ""
    except (OSError, pyudev.DeviceNotFoundError):
        pass
    has_mt = _ABS_MT_POSITION_X in caps.get(_EV_ABS, ())
    has_finger = _BTN_TOOL_FINGER in caps.get(_EV_KEY, ())
    return (True, "capability") if has_mt and has_finger else (False, "")

async def read_device(dev: evdev.InputDevice) -> None:
    _play = play_sound
    dev_name = dev.name or "<unnamed>"

    print(f"{C_GREEN}[+] Connected:{C_RESET} {dev_name} {C_DIM}({dev.path}){C_RESET}")
    try:
        # One await per kernel batch avoids a Future for every SYN/REL event.
        while True:
            # evdev's ready callback can complete a read during cancellation.
            # Shield its Future; finally still closes/removes the fd reader.
            for event in await asyncio.shield(dev.async_read()):
                if event.type == _EV_KEY and event.value == 1:
                    _play(event.code)
    except OSError:
        print(f"{C_YELLOW}[-] Disconnected:{C_RESET} {dev.path}")
    finally:
        try:
            dev.close()
        except OSError:
            pass

def prune_dead_tasks(monitored_tasks: dict[str, asyncio.Task[None]]) -> None:
    dead_paths = [path for path, task in monitored_tasks.items() if task.done()]
    for path in dead_paths:
        task = monitored_tasks.pop(path)
        try:
            exc = task.exception()
        except asyncio.CancelledError:
            exc = None
        if exc is not None:
            raise exc

def scan_devices(
    monitored_tasks: dict[str, asyncio.Task[None]],
    skipped_paths: dict[str, int],
) -> None:
    prune_dead_tasks(monitored_tasks)

    all_paths = evdev.list_devices(writable=False)
    current_set = set(all_paths)

    # eventN paths can be reused between polls. Cache the node identity,
    # so a replacement keyboard does not inherit a removed touchpad's skip.
    for path, inode in tuple(skipped_paths.items()):
        try:
            same_node = path in current_set and os.stat(path).st_ino == inode
        except OSError:
            same_node = False
        if not same_node:
            del skipped_paths[path]

    for path in all_paths:
        if path in monitored_tasks or path in skipped_paths:
            continue

        dev: evdev.InputDevice | None = None
        try:
            dev = evdev.InputDevice(path, readonly=True)
            caps = dev.capabilities(absinfo=False)

            if _EV_KEY not in caps:
                skipped_paths[path] = os.fstat(dev.fd).st_ino
                continue

            if not ENABLE_TRACKPADS:
                dev_name = dev.name or "<unnamed>"
                name_lower = dev_name.casefold()
                keyword_match = any(keyword in name_lower for keyword in EXCLUDED_KEYWORDS)

                if keyword_match:
                    print(f"{C_DIM}[~] Skipped: {dev_name} ({dev.path}) [keyword]{C_RESET}")
                    skipped_paths[path] = os.fstat(dev.fd).st_ino
                    continue

                if AUTO_DETECT:
                    is_touchpad, source = classify_touchpad(path, caps)
                    if is_touchpad:
                        print(f"{C_DIM}[~] Skipped: {dev_name} ({dev.path}) [{source} touchpad]{C_RESET}")
                        skipped_paths[path] = os.fstat(dev.fd).st_ino
                        continue

            monitored_tasks[path] = asyncio.create_task(read_device(dev))
            dev = None

        except OSError:
            continue
        finally:
            if dev is not None:
                try:
                    dev.close()
                except OSError:
                    pass

    prune_dead_tasks(monitored_tasks)

async def main() -> None:
    loop_type = "uvloop (native)" if uvloop is not None else "asyncio (standard)"
    print(f"{C_BLUE}[CORE]{C_RESET}  Engine started | Event loop: {loop_type}")

    if ENABLE_TRACKPADS:
        filter_mode = "disabled (all devices play sounds)"
    else:
        filter_mode = f"keyword blacklist ({len(EXCLUDED_KEYWORDS)} entries)"
        filter_mode += " + auto-detect" if AUTO_DETECT else " only"

    print(f"{C_BLUE}[CORE]{C_RESET}  Filtering: {filter_mode}")
    print(f"{C_BLUE}[CORE]{C_RESET}  Monitoring devices (poll: {POLL_INTERVAL}s)...")

    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, stop.set)

    monitored_tasks: dict[str, asyncio.Task[None]] = {}
    skipped_paths: dict[str, int] = {}

    try:
        scan_devices(monitored_tasks, skipped_paths)
        if not monitored_tasks:
            sys.exit(f"{C_RED}[INPUT ERROR]{C_RESET} No readable, eligible EV_KEY devices.")
        mark_ready(READY_FILE)

        while not stop.is_set():
            try:
                await asyncio.wait_for(stop.wait(), timeout=POLL_INTERVAL)
            except TimeoutError:
                scan_devices(monitored_tasks, skipped_paths)

        print("\nStopping...")

    finally:
        for sig in (signal.SIGINT, signal.SIGTERM):
            loop.remove_signal_handler(sig)
        tasks = tuple(monitored_tasks.values())
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        pygame.mixer.quit()

if __name__ == "__main__":
    if uvloop is not None:
        uvloop.run(main())
    else:
        asyncio.run(main())
PYTHON_EOF
# Avoid rewriting an identical runner.
if ! cmp -s -- "$RUNNER_TMP" "$RUNNER_SCRIPT"; then
    mv -f -- "$RUNNER_TMP" "$RUNNER_SCRIPT"
else
    rm -f -- "$RUNNER_TMP"
fi
RUNNER_TMP=''
if [[ $RUN_MODE == setup ]]; then
    printf '[SETUP] Python 3.15 environment ready.\n'
    exit 0
fi
[[ -r $CONFIG_DIR/$AUDIO_PACK/config.json ]] || fail "Missing $CONFIG_DIR/$AUDIO_PACK/config.json"
prepare_input_access
# Check every service in one query: is-active with several units succeeds when
# just one is active. Starting an active unit does not restart desktop audio.
service_states=$(systemctl --user show -p ActiveState --value \
    pipewire.service pipewire-pulse.service wireplumber.service 2>/dev/null) || service_states=''
active_count=0
while IFS= read -r service_state; do
    [[ $service_state != active ]] || (( active_count+=1 ))
done <<< "$service_states"
if (( active_count != 3 )); then
    systemctl --user daemon-reload
    systemctl --user start pipewire.service pipewire-pulse.service wireplumber.service \
        || restart_for_setup
fi

EXCLUDED_KW_STR=$(IFS=,; printf '%s' "${EXCLUDED_KEYWORDS[*]}")
ENABLE_TRACKPADS="$ENABLE_TRACKPAD_SOUNDS" \
WC_AUTO_DETECT="$AUTO_DETECT_TRACKPADS" \
WC_EXCLUDED_KEYWORDS="$EXCLUDED_KW_STR" \
WC_AUDIO_BUFFER="$AUDIO_BUFFER_SIZE" \
WC_AUDIO_RATE="$AUDIO_SAMPLE_RATE" \
WC_MIX_CHANNELS="$AUDIO_MIX_CHANNELS" \
WC_POLL_INTERVAL="$HOTPLUG_POLL_SECONDS" \
WC_DEBUG="$DEBUG_MODE" \
WC_READY_FILE="$READY_FILE" \
PIPEWIRE_LATENCY="${AUDIO_BUFFER_SIZE}/${AUDIO_SAMPLE_RATE}" \
"$PYTHON_BIN" -B "$RUNNER_SCRIPT" "$CONFIG_DIR" "$AUDIO_PACK" {LOCK_FD}>&- &
CHILD_PID=$!
# Record identity immediately; cmdline can still describe bash before exec.
if proc_identity "$CHILD_PID"; then
    CHILD_START=$REPLY
else
    wait "$CHILD_PID" || exit "$?"
    fail 'Runner exited before startup.'
fi
printf '%s %s\n' "$CHILD_PID" "$CHILD_START" > "$PID_FILE"
startup_ok=false
for (( i=0; i<300; i++ )); do
    if [[ -f $READY_FILE ]]; then
        startup_ok=true
        break
    fi
    proc_identity "$CHILD_PID" && [[ $REPLY == "$CHILD_START" ]] || break
    sleep 0.05
done
if ! $startup_ok; then
    stop_runner "$CHILD_PID" "$CHILD_START"
    runner_status=0
    wait "$CHILD_PID" || runner_status=$?
    runtime_ready || restart_for_setup
    (( runner_status == 0 )) || exit "$runner_status"
    fail 'Runner did not confirm readiness within 15 seconds.'
fi
update_state True
rm -f -- "$READY_FILE"
release_lock
notify_user "Enabled ($AUDIO_PACK)"
wait "$CHILD_PID" || exit "$?"
