#!/usr/bin/env bash
# ==============================================================================
# WAYCLICK ELITE - ARCH LINUX / UV OPTIMIZED (GOLDEN EDITION)
# ==============================================================================
# "I fear not the man who has practiced 10,000 kicks once,
#  but I fear the man who has practiced one kick 10,000 times." - Bruce Lee
# ==============================================================================
#
#  ENABLE_TRACKPAD_SOUNDS="true"
#    → ALL devices with EV_KEY play sounds. No filtering whatsoever.
#
#  ENABLE_TRACKPAD_SOUNDS="false" + AUTO_DETECT_TRACKPADS="true"  (default)
#    → Keyword blacklist filters named devices
#    → udev touchpad detection catches properly tagged devices
#    → Capability fallback catches some unnamed touchpads
#    → Both filters active
#
#  ENABLE_TRACKPAD_SOUNDS="false" + AUTO_DETECT_TRACKPADS="false"
#    → ONLY keyword blacklist is used
#    → Remove a keyword → that device type is un-blocked
#    → Full manual control
#

set -euo pipefail
shopt -s inherit_errexit

# --- ARGUMENT PARSING (before trap so RUN_MODE is always set) ---
RUN_MODE="run"
case "${1:-}" in
    --reset) RUN_MODE="reset" ;;
    --setup) RUN_MODE="setup" ;;
    --help|-h)
        SCRIPT_NAME="$(basename -- "$0")"
        printf "Usage: %s [--setup|--reset]\n\n" "$SCRIPT_NAME"
        printf "  (no args)  Start or stop WayClick (toggle)\n"
        printf "  --setup    Install dependencies and build the environment only\n"
        printf "  --reset    Stop WayClick and delete the environment\n"
        exit 0
        ;;
    "") ;;
    *)
        SCRIPT_NAME="$(basename -- "$0")"
        printf "Unknown option: %s\nRun '%s --help' for usage.\n" "$1" "$SCRIPT_NAME"
        exit 1
        ;;
esac

# ╔════════════════════════════════════════════════════════════════════════════╗
# ║  USER CONFIGURATION — Tune these to your preference                      ║
# ╚════════════════════════════════════════════════════════════════════════════╝

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

# Auto-detect touchpads by udev tags first, then a capability fallback.
# Only active when ENABLE_TRACKPAD_SOUNDS is "false".
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

# ╔════════════════════════════════════════════════════════════════════════════╗
# ║  INTERNAL CONFIGURATION — Change only if you know what you're doing      ║
# ╚════════════════════════════════════════════════════════════════════════════╝

readonly APP_NAME="wayclick"
readonly SCRIPT_NAME="$(basename -- "$0")"
readonly BASE_DIR="$HOME/contained_apps/uv/$APP_NAME"
readonly VENV_DIR="$BASE_DIR/.venv"
readonly PYTHON_BIN="$VENV_DIR/bin/python"
readonly RUNNER_SCRIPT="$BASE_DIR/runner.py"
readonly CONFIG_DIR="$HOME/.config/wayclick"
readonly STATE_FILE="$HOME/.config/dusky/settings/wayclick"
readonly PID_FILE="$BASE_DIR/$APP_NAME.pid"
readonly LOCK_FILE="$BASE_DIR/$APP_NAME.lock"
readonly MARKER_FILE="$BASE_DIR/.build_marker_v10"

# --- ANSI COLORS ---
readonly C_RED=$'\033[1;31m'
readonly C_GREEN=$'\033[1;32m'
readonly C_BLUE=$'\033[1;34m'
readonly C_CYAN=$'\033[1;36m'
readonly C_YELLOW=$'\033[1;33m'
readonly C_DIM=$'\033[2m'
readonly C_RESET=$'\033[0m'

RUNNER_CHILD_PID=""
LOCK_FD=""

# --- UTILITY FUNCTIONS ---

update_state() {
    local status="$1"
    local dir tmp_file

    dir="${STATE_FILE%/*}"
    mkdir -p "$dir" 2>/dev/null || true

    tmp_file="$(mktemp "$dir/.wayclick.state.XXXXXX")"
    printf '%s\n' "$status" > "$tmp_file"
    mv -f "$tmp_file" "$STATE_FILE"
}

write_pid_file() {
    local pid="$1"
    local tmp_file

    mkdir -p "$BASE_DIR" 2>/dev/null || true
    tmp_file="$(mktemp "$BASE_DIR/.wayclick.pid.XXXXXX")"
    printf '%s\n' "$pid" > "$tmp_file"
    mv -f "$tmp_file" "$PID_FILE"
}

clear_pid_file_if_owned_by_child() {
    local pid
    [[ -n "${RUNNER_CHILD_PID:-}" ]] || return 0
    [[ -r "$PID_FILE" ]] || return 0

    if read -r pid < "$PID_FILE" && [[ "$pid" == "$RUNNER_CHILD_PID" ]]; then
        rm -f "$PID_FILE" 2>/dev/null || true
    fi
}

cleanup() {
    tput cnorm 2>/dev/null || true

    if [[ -n "${RUNNER_CHILD_PID:-}" ]] && kill -0 "$RUNNER_CHILD_PID" 2>/dev/null; then
        kill -TERM "$RUNNER_CHILD_PID" 2>/dev/null || true
    fi

    clear_pid_file_if_owned_by_child

    if [[ "${RUN_MODE:-run}" != "setup" ]] && [[ -n "${STATE_FILE:-}" ]]; then
        update_state "False"
    fi
}

notify_user() {
    if command -v notify-send >/dev/null 2>&1; then
        notify-send -t 2000 --app-name="WayClick" "WayClick Elite" "$1" >/dev/null 2>&1 || true
    fi
}

acquire_lock() {
    mkdir -p "$BASE_DIR" 2>/dev/null || true
    exec {LOCK_FD}> "$LOCK_FILE"
    flock -w 30 "$LOCK_FD"
}

release_lock() {
    [[ -n "${LOCK_FD:-}" ]] || return 0
    flock -u "$LOCK_FD" 2>/dev/null || true
}

pid_owned_by_current_user() {
    local pid="$1"
    local uid

    [[ "$pid" =~ ^[0-9]+$ ]] || return 1
    [[ -r "/proc/$pid/status" ]] || return 1

    uid="$(awk '/^Uid:/ {print $2}' "/proc/$pid/status" 2>/dev/null || true)"
    [[ "$uid" == "$EUID" ]]
}

pid_argv_contains_exact_arg() {
    local pid="$1"
    local wanted="$2"
    local arg

    [[ -r "/proc/$pid/cmdline" ]] || return 1

    while IFS= read -r -d '' arg; do
        [[ "$arg" == "$wanted" ]] && return 0
    done < "/proc/$pid/cmdline"

    return 1
}

pid_is_wayclick_runner() {
    local pid="$1"
    pid_owned_by_current_user "$pid" &&
        pid_argv_contains_exact_arg "$pid" "$RUNNER_SCRIPT" &&
        pid_argv_contains_exact_arg "$pid" "$PYTHON_BIN"
}

find_runner_pids() {
    local pid proc pid_num
    declare -A seen=()

    if [[ -r "$PID_FILE" ]]; then
        if read -r pid < "$PID_FILE" && [[ "$pid" =~ ^[0-9]+$ ]] && pid_is_wayclick_runner "$pid"; then
            seen["$pid"]=1
        else
            rm -f "$PID_FILE" 2>/dev/null || true
        fi
    fi

    shopt -s nullglob
    for proc in /proc/[0-9]*/cmdline; do
        pid_num="${proc#/proc/}"
        pid_num="${pid_num%/cmdline}"
        [[ -n "${seen[$pid_num]+x}" ]] && continue
        pid_is_wayclick_runner "$pid_num" || continue
        seen["$pid_num"]=1
    done
    shopt -u nullglob

    ((${#seen[@]} > 0)) || return 1
    printf '%s\n' "${!seen[@]}" | sort -n
}

stop_runner_pids() {
    local -a pids=("$@")
    local wait_count=0
    local any_alive
    local pid

    ((${#pids[@]} > 0)) || return 0

    for pid in "${pids[@]}"; do
        kill -TERM "$pid" 2>/dev/null || true
    done

    while (( wait_count++ < 30 )); do
        any_alive=false
        for pid in "${pids[@]}"; do
            if kill -0 "$pid" 2>/dev/null; then
                any_alive=true
                break
            fi
        done
        $any_alive || break
        sleep 0.1
    done

    for pid in "${pids[@]}"; do
        if kill -0 "$pid" 2>/dev/null; then
            kill -KILL "$pid" 2>/dev/null || true
        fi
    done

    rm -f "$PID_FILE" 2>/dev/null || true
    update_state "False"
}

pipewire_services_active() {
    local svc
    for svc in pipewire.service pipewire-pulse.service wireplumber.service; do
        systemctl --user --quiet is-active "$svc" >/dev/null 2>&1 || return 1
    done
}

activate_pipewire_services() {
    local action="start"
    local -a services=("pipewire.service" "pipewire-pulse.service" "wireplumber.service")

    if $AUDIO_PKGS_INSTALLED; then
        action="restart"
    fi

    printf "%b[AUDIO]%b Ensuring PipeWire audio services are active...\n" "${C_BLUE}" "${C_RESET}"

    systemctl --user daemon-reload >/dev/null 2>&1 || true

    if ! systemctl --user enable "${services[@]}" >/dev/null 2>&1; then
        printf "%b[WARN]%b Could not enable one or more PipeWire user services.\n" \
            "${C_YELLOW}" "${C_RESET}"
    fi

    if ! systemctl --user "$action" "${services[@]}" >/dev/null 2>&1; then
        printf "%b[WARN]%b Could not fully %s PipeWire services. Audio may fail until the session is restarted.\n" \
            "${C_YELLOW}" "${C_RESET}" "$action"
    fi

    sleep 1
}

python_runtime_ready() {
    [[ -x "$PYTHON_BIN" ]] || return 1
    PYGAME_HIDE_SUPPORT_PROMPT=1 "$PYTHON_BIN" - <<'PY' >/dev/null 2>&1
import evdev
import pygame
PY
}

trap cleanup EXIT INT TERM

# --- 0. ROOT CHECK ---
if (( EUID == 0 )); then
    printf "%b[CRITICAL]%b Do not run this script as root.\n" "${C_RED}" "${C_RESET}"
    exit 1
fi

# --- 1. INTERACTIVE DETECTION ---
if [[ -t 0 && -t 1 ]]; then
    INTERACTIVE=true
else
    INTERACTIVE=false
fi

# --- 2. LOCK ---
acquire_lock

# --- 3. RESET MODE ---
if [[ "$RUN_MODE" == "reset" ]]; then
    declare -a runner_pids=()
    mapfile -t runner_pids < <(find_runner_pids || true)

    if (( ${#runner_pids[@]} > 0 )); then
        printf "%b[RESET]%b Stopping running instance...\n" "${C_YELLOW}" "${C_RESET}"
        notify_user "Disabled"
        stop_runner_pids "${runner_pids[@]}"
    fi

    cleaned_any=false

    if [[ -d "$VENV_DIR" ]]; then
        rm -rf "$VENV_DIR"
        cleaned_any=true
    fi

    if compgen -G "$BASE_DIR/.build_marker_*" >/dev/null; then
        rm -f "$BASE_DIR"/.build_marker_*
        cleaned_any=true
    fi

    if [[ -f "$RUNNER_SCRIPT" ]]; then
        rm -f "$RUNNER_SCRIPT"
        cleaned_any=true
    fi

    if [[ -f "$PID_FILE" ]]; then
        rm -f "$PID_FILE"
        cleaned_any=true
    fi

    if [[ -f "$LOCK_FILE" ]]; then
        rm -f "$LOCK_FILE" 2>/dev/null || true
    fi

    if $cleaned_any; then
        printf "%b[RESET]%b Environment deleted successfully.\n" "${C_GREEN}" "${C_RESET}"
    else
        printf "%b[RESET]%b Nothing to clean (environment not found).\n" "${C_BLUE}" "${C_RESET}"
    fi

    exit 0
fi

# --- 4. TOGGLE STOP (run mode only) ---
if [[ "$RUN_MODE" == "run" ]]; then
    declare -a runner_pids=()
    mapfile -t runner_pids < <(find_runner_pids || true)

    if (( ${#runner_pids[@]} > 0 )); then
        printf "%b[TOGGLE]%b Stopping active instance...\n" "${C_YELLOW}" "${C_RESET}"
        notify_user "Disabled"
        stop_runner_pids "${runner_pids[@]}"
        exit 0
    fi
fi

# --- 5. DEPENDENCY CHECK ---
declare -a NEEDED_DEPS=()
declare -A _dep_seen=()
AUDIO_PKGS_INSTALLED=false

append_dep() {
    local dep="$1"
    if [[ -z "${_dep_seen[$dep]+x}" ]]; then
        NEEDED_DEPS+=("$dep")
        _dep_seen["$dep"]=1
    fi
}

command -v uv >/dev/null 2>&1          || append_dep "uv"
command -v notify-send >/dev/null 2>&1 || append_dep "libnotify"

# Runtime audio stack.
audio_deps=("pipewire" "pipewire-audio" "pipewire-pulse" "wireplumber")
for dep in "${audio_deps[@]}"; do
    if ! pacman -Qq "$dep" >/dev/null 2>&1; then
        append_dep "$dep"
        AUDIO_PKGS_INSTALLED=true
    fi
done

# Build-time deps.
command -v gcc >/dev/null 2>&1 || append_dep "gcc"

build_deps=("sdl2" "sdl2_mixer" "sdl2_image" "sdl2_ttf" "portmidi" "freetype2" "pkgconf" "libuv")
for dep in "${build_deps[@]}"; do
    pacman -Qq "$dep" >/dev/null 2>&1 || append_dep "$dep"
done

if (( ${#NEEDED_DEPS[@]} > 0 )); then
    if $INTERACTIVE; then
        clear
        printf "%b
╔════════════════════════════════════════════════════════════════╗
║  %bWAYCLICK ELITE%b                                                ║
║  %bHotplug • User Mode • Native CPU • Contained%b                  ║
╚════════════════════════════════════════════════════════════════╝
%b" "${C_CYAN}" "${C_GREEN}" "${C_CYAN}" "${C_DIM}" "${C_CYAN}" "${C_RESET}"

        printf "%b[SETUP]%b Missing system dependencies:%b %s%b\n" \
            "${C_YELLOW}" "${C_RESET}" "${C_CYAN}" "${NEEDED_DEPS[*]}" "${C_RESET}"
        printf "       Requesting sudo to install via pacman...\n"

        if sudo pacman -S --needed --noconfirm "${NEEDED_DEPS[@]}"; then
            printf "%b[SUCCESS]%b Dependencies installed.\n" "${C_GREEN}" "${C_RESET}"
        else
            printf "%b[ERROR]%b Installation failed.\n" "${C_RED}" "${C_RESET}"
            exit 1
        fi
    else
        notify_user "Missing dependencies (${NEEDED_DEPS[*]}). Run in terminal first."
        exit 1
    fi
fi

# --- 6. PIPEWIRE SERVICE ACTIVATION ---
if $AUDIO_PKGS_INSTALLED || ! pipewire_services_active; then
    activate_pipewire_services
fi

# --- 7. RUNTIME CONFIG CHECKS (run mode only) ---
if [[ "$RUN_MODE" == "run" ]]; then
    if [[ ! -f "${CONFIG_DIR}/config.json" ]]; then
        if $INTERACTIVE; then
            mkdir -p "$CONFIG_DIR" 2>/dev/null || true
            while [[ ! -f "${CONFIG_DIR}/config.json" ]]; do
                printf "\n%b[ACTION REQUIRED]%b Missing config.json in: %s\n" \
                    "${C_YELLOW}" "${C_RESET}" "${CONFIG_DIR}"
                printf "       Please ensure 'config.json' exists in this folder.\n"
                printf "       %bPress Enter to re-scan...%b" "${C_DIM}" "${C_RESET}"
                read -r
            done
            printf "%b[CHECK]%b Configuration found.\n" "${C_GREEN}" "${C_RESET}"
        else
            notify_user "Missing config.json in ~/.config/wayclick. Run in terminal."
            exit 1
        fi
    fi

    if [[ ! -d "${CONFIG_DIR}/${AUDIO_PACK}" ]]; then
        if $INTERACTIVE; then
            printf "\n%b[ERROR]%b Audio pack '%b%s%b' not found in: %s\n" \
                "${C_RED}" "${C_RESET}" "${C_CYAN}" "$AUDIO_PACK" "${C_RESET}" "${CONFIG_DIR}"

            mapfile -t available < <(
                find "$CONFIG_DIR" -mindepth 1 -maxdepth 1 -type d -printf '%f\n' 2>/dev/null | sort
            )

            if (( ${#available[@]} > 0 )); then
                printf "       Available packs:\n"
                for pack in "${available[@]}"; do
                    printf "         %b→%b %s\n" "${C_CYAN}" "${C_RESET}" "$pack"
                done
                printf "\n       Update %bAUDIO_PACK%b at the top of this script to one of the above.\n" \
                    "${C_GREEN}" "${C_RESET}"
            else
                printf "       No audio packs found. Create a subdirectory with .wav files:\n"
                printf "         %bmkdir -p %s/my_sounds && cp *.wav %s/my_sounds/%b\n" \
                    "${C_DIM}" "${CONFIG_DIR}" "${CONFIG_DIR}" "${C_RESET}"
            fi
            exit 1
        else
            notify_user "Audio pack '$AUDIO_PACK' not found. Run in terminal."
            exit 1
        fi
    fi
fi

# --- 8. ENVIRONMENT SETUP ---
mkdir -p "$BASE_DIR" 2>/dev/null || true

if [[ ! -x "$PYTHON_BIN" ]]; then
    rm -rf "$VENV_DIR" 2>/dev/null || true

    if ! $INTERACTIVE; then
        notify_user "Environment not built. Run in terminal once to initialize."
        exit 1
    fi

    printf "%b[BUILD]%b Initializing UV environment...\n" "${C_BLUE}" "${C_RESET}"
    uv venv "$VENV_DIR" --python 3.14 --quiet
fi

if [[ -f "$MARKER_FILE" ]] && ! python_runtime_ready; then
    rm -f "$MARKER_FILE"
fi

if [[ ! -f "$MARKER_FILE" ]]; then
    if ! $INTERACTIVE; then
        notify_user "First run setup required. Run in terminal to build native extensions."
        exit 1
    fi

    printf "%b[BUILD]%b Compiling dependencies with native CPU flags...\n" \
        "${C_YELLOW}" "${C_RESET}"

    export CFLAGS="-march=native -mtune=native -O3 -pipe -fno-plt -fno-semantic-interposition -fno-math-errno -fno-trapping-math -flto=auto -ffat-lto-objects -ffp-contract=fast -DNDEBUG"
    export CXXFLAGS="$CFLAGS"
    export LDFLAGS="-Wl,-O2,--sort-common,--as-needed,-z,now,--relax -flto=auto"

    uv pip install --python "$PYTHON_BIN" \
        --upgrade \
        --no-binary evdev \
        --no-binary pygame-ce \
        --no-cache \
        --compile-bytecode \
        evdev pygame-ce

    printf "%b[BUILD]%b Attempting uvloop (optional)...\n" "${C_BLUE}" "${C_RESET}"
    uv pip install --python "$PYTHON_BIN" \
        --upgrade \
        --no-binary uvloop \
        --no-cache \
        --compile-bytecode \
        uvloop >/dev/null 2>&1 \
        && printf "%b[SUCCESS]%b uvloop installed.\n" "${C_GREEN}" "${C_RESET}" \
        || printf "%b[INFO]%b uvloop skipped. Standard asyncio will be used.\n" "${C_YELLOW}" "${C_RESET}"

    if ! python_runtime_ready; then
        printf "%b[ERROR]%b Python runtime validation failed after build.\n" "${C_RED}" "${C_RESET}"
        exit 1
    fi

    touch "$MARKER_FILE"
    printf "%b[SUCCESS]%b Native build complete.\n" "${C_GREEN}" "${C_RESET}"
fi

# --- 9. PYTHON RUNNER GENERATION ---
runner_tmp="$(mktemp "$BASE_DIR/.runner.XXXXXX")"
cat > "$runner_tmp" << 'PYTHON_EOF'
from __future__ import annotations

import asyncio
import gc
import json
import os
import random
import shutil
import signal
import subprocess
import sys
from pathlib import Path

# === FAST EVENT LOOP ===
try:
    import uvloop
except ImportError:
    uvloop = None

# === EARLY ENVIRONMENT ===
os.environ["PYGAME_HIDE_SUPPORT_PROMPT"] = "1"
os.environ["SDL_AUDIODRIVER"] = "pipewire,pulseaudio,alsa"

import evdev
import pygame

sys.stdout.reconfigure(line_buffering=True)
sys.stderr.reconfigure(line_buffering=True)

C_GREEN  = "\033[1;32m"
C_YELLOW = "\033[1;33m"
C_BLUE   = "\033[1;34m"
C_RED    = "\033[1;31m"
C_DIM    = "\033[2m"
C_RESET  = "\033[0m"

# === ARGUMENTS ===
if len(sys.argv) != 3:
    sys.exit(f"{C_RED}[USAGE ERROR]{C_RESET} runner.py <config_dir> <pack_name>")

CONFIG_DIR = Path(sys.argv[1]).expanduser()
PACK_NAME = sys.argv[2]
ASSET_DIR = CONFIG_DIR / PACK_NAME
CONFIG_FILE = CONFIG_DIR / "config.json"

# === ENV PARSING ===
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
    if value <= minimum_exclusive:
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

# === INPUT CONSTANTS ===
_EV_KEY = 1
_EV_ABS = 3
_ABS_MT_POSITION_X = 0x35
_BTN_TOOL_FINGER = 0x145

# === AUDIO INIT ===
try:
    pygame.mixer.pre_init(frequency=SAMPLE_RATE, size=-16, channels=2, buffer=BUFFER_SIZE)
    pygame.mixer.init()
    pygame.mixer.set_num_channels(MIX_CHANNELS)
except pygame.error as exc:
    sys.exit(f"{C_RED}[AUDIO ERROR]{C_RESET} {exc}")

latency_ms = BUFFER_SIZE / SAMPLE_RATE * 1000.0
print(
    f"{C_BLUE}[AUDIO]{C_RESET} Buffer={BUFFER_SIZE} samples (~{latency_ms:.1f}ms) | "
    f"Rate={SAMPLE_RATE}Hz | Channels={MIX_CHANNELS}"
)

# === CONFIG LOAD ===
print(f"{C_BLUE}[INFO]{C_RESET}  Config: {CONFIG_FILE}")
print(f"{C_BLUE}[INFO]{C_RESET}  Pack:   {ASSET_DIR}")

try:
    with CONFIG_FILE.open("r", encoding="utf-8") as fh:
        config_data = json.load(fh)
except Exception as exc:
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

# === SOUND LOAD ===
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

# === PERFORMANCE CACHE ===
MAX_KEYCODE = max(1024, (max(RAW_KEY_MAP, default=0) + 1))
SOUND_CACHE: list[pygame.mixer.Sound | None] = [None] * MAX_KEYCODE
DEFAULT_SOUND_OBJS = tuple(SOUNDS[name] for name in DEFAULTS if name in SOUNDS)

for code, filename in RAW_KEY_MAP.items():
    if code < MAX_KEYCODE and filename in SOUNDS:
        SOUND_CACHE[code] = SOUNDS[filename]

# === LONG-LIVED OBJECTS ONLY FROM HERE: DISABLE CYCLIC GC ===
gc.disable()

# === HOT PATH PRE-BINDING ===
_random_choice = random.choice
_sound_cache = SOUND_CACHE
_max_keycode = MAX_KEYCODE
_defaults = DEFAULT_SOUND_OBJS
_has_defaults = bool(DEFAULT_SOUND_OBJS)

if DEBUG:
    import time
    _perf = time.perf_counter_ns

    def play_sound(code: int) -> None:
        t0 = _perf()
        sound = _sound_cache[code] if code < _max_keycode else None
        if sound is not None:
            sound.play()
        elif _has_defaults:
            _random_choice(_defaults).play()
        elapsed_us = (_perf() - t0) / 1000.0
        print(f"  \u23f1 {elapsed_us:.1f}\u00b5s [code={code}]")
else:
    def play_sound(code: int) -> None:
        sound = _sound_cache[code] if code < _max_keycode else None
        if sound is not None:
            sound.play()
        elif _has_defaults:
            _random_choice(_defaults).play()

# === TOUCHPAD DETECTION ===
_UDEVADM = shutil.which("udevadm")

def get_udev_properties(path: str) -> dict[str, str] | None:
    if _UDEVADM is None:
        return None

    try:
        proc = subprocess.run(
            [_UDEVADM, "info", "--query=property", "--name", path],
            capture_output=True,
            text=True,
            timeout=1.5,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return None

    if proc.returncode != 0:
        return None

    props: dict[str, str] = {}
    for line in proc.stdout.splitlines():
        if "=" not in line:
            continue
        key, _, value = line.partition("=")
        props[key] = value
    return props

def classify_touchpad(
    path: str,
    caps: dict[int, list[int]],
    udev_cache: dict[str, dict[str, str] | None],
) -> tuple[bool, str]:
    props = udev_cache.get(path)
    if path not in udev_cache:
        props = get_udev_properties(path)
        udev_cache[path] = props

    if props:
        if props.get("ID_INPUT_TOUCHPAD") == "1":
            return True, "udev"
        if props.get("ID_INPUT_TOUCHSCREEN") == "1":
            return False, ""

    abs_codes = caps.get(_EV_ABS, [])
    key_codes = caps.get(_EV_KEY, [])
    has_mt = _ABS_MT_POSITION_X in abs_codes
    has_finger = _BTN_TOOL_FINGER in key_codes

    if has_mt and has_finger:
        return True, "capability"

    return False, ""

# === DEVICE READER ===
async def read_device(dev: evdev.InputDevice, stop_event: asyncio.Event) -> None:
    _play = play_sound
    _is_stopped = stop_event.is_set
    dev_name = dev.name or "<unnamed>"

    print(f"{C_GREEN}[+] Connected:{C_RESET} {dev_name} {C_DIM}({dev.path}){C_RESET}")
    try:
        async for event in dev.async_read_loop():
            if _is_stopped():
                break
            if event.type == _EV_KEY and event.value == 1:
                _play(event.code)
    except OSError:
        print(f"{C_YELLOW}[-] Disconnected:{C_RESET} {dev.path}")
    except asyncio.CancelledError:
        pass
    finally:
        try:
            dev.close()
        except OSError:
            pass

# === MAIN LOOP ===
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
    skipped_paths: set[str] = set()
    udev_cache: dict[str, dict[str, str] | None] = {}
    _list_devices = evdev.list_devices

    while not stop.is_set():
        all_paths = _list_devices()
        current_set = set(all_paths)

        skipped_paths &= current_set

        stale_cached = set(udev_cache) - current_set
        for stale_path in stale_cached:
            udev_cache.pop(stale_path, None)

        for path in all_paths:
            if path in monitored_tasks or path in skipped_paths:
                continue

            try:
                dev = evdev.InputDevice(path)
                caps = dev.capabilities(absinfo=False)
            except OSError:
                continue

            try:
                if not ENABLE_TRACKPADS:
                    name_lower = (dev.name or "").casefold()
                    keyword_match = any(keyword in name_lower for keyword in EXCLUDED_KEYWORDS)

                    if keyword_match:
                        print(f"{C_DIM}[~] Skipped: {dev.name} ({dev.path}) [keyword]{C_RESET}")
                        skipped_paths.add(path)
                        continue

                    if AUTO_DETECT:
                        is_touchpad, source = classify_touchpad(path, caps, udev_cache)
                        if is_touchpad:
                            print(f"{C_DIM}[~] Skipped: {dev.name} ({dev.path}) [{source} touchpad]{C_RESET}")
                            skipped_paths.add(path)
                            continue

                if _EV_KEY in caps:
                    monitored_tasks[path] = asyncio.create_task(read_device(dev, stop))
                else:
                    skipped_paths.add(path)
            finally:
                if path not in monitored_tasks:
                    try:
                        dev.close()
                    except OSError:
                        pass

        dead_paths = [path for path, task in monitored_tasks.items() if task.done()]
        for path in dead_paths:
            task = monitored_tasks.pop(path)
            try:
                exc = task.exception()
            except asyncio.CancelledError:
                exc = None
            if exc is not None:
                raise exc

        try:
            await asyncio.wait_for(stop.wait(), timeout=POLL_INTERVAL)
        except asyncio.TimeoutError:
            pass

    print("\nStopping...")

    for task in monitored_tasks.values():
        task.cancel()

    if monitored_tasks:
        await asyncio.gather(*monitored_tasks.values(), return_exceptions=True)

    pygame.mixer.quit()

if __name__ == "__main__":
    try:
        if uvloop is not None:
            uvloop.run(main())
        else:
            asyncio.run(main())
    except KeyboardInterrupt:
        pass
PYTHON_EOF
mv -f "$runner_tmp" "$RUNNER_SCRIPT"

# --- 10. SETUP MODE EXIT ---
if [[ "$RUN_MODE" == "setup" ]]; then
    printf "\n%b[SETUP]%b Setup complete! Run '%b%s%b' to start WayClick.\n" \
        "${C_GREEN}" "${C_RESET}" "${C_CYAN}" "$SCRIPT_NAME" "${C_RESET}"

    if ! id -nG "$USER" | grep -qw input; then
        printf "%b[NOTE]%b  User '%s' is not in the 'input' group (required to run).\n" \
            "${C_YELLOW}" "${C_RESET}" "$USER"
        printf "        Run: %bsudo usermod -aG input %s%b (then logout/login)\n" \
            "${C_CYAN}" "$USER" "${C_RESET}"
    fi

    exit 0
fi

# --- 11. GROUP PERMISSION CHECK (run mode only) ---
if ! id -nG "$USER" | grep -qw input; then
    if $INTERACTIVE; then
        printf "%b[PERM]%b User '%s' is not in the 'input' group.\n" \
            "${C_RED}" "${C_RESET}" "$USER"
        read -rp "Run 'sudo usermod -aG input $USER'? [Y/n] " -n 1
        echo
        if [[ ${REPLY:-Y} =~ ^[Yy]$ ]]; then
            sudo usermod -aG input "$USER"
            printf "%b[INFO]%b Group added. %bLOGOUT REQUIRED%b for changes to apply.\n" \
                "${C_GREEN}" "${C_RESET}" "${C_RED}" "${C_RESET}"
            exit 0
        fi
        exit 1
    else
        notify_user "Permission error: user not in 'input' group. Run in terminal."
        exit 1
    fi
fi

# --- 12. EXECUTION ---
printf "%b[RUN]%b Starting engine (pack: %b%s%b | buffer: %s samples)...\n" \
    "${C_BLUE}" "${C_RESET}" "${C_CYAN}" "$AUDIO_PACK" "${C_RESET}" "$AUDIO_BUFFER_SIZE"

EXCLUDED_KW_STR="$(IFS=,; printf '%s' "${EXCLUDED_KEYWORDS[*]}")"

ENABLE_TRACKPADS="$ENABLE_TRACKPAD_SOUNDS" \
WC_AUTO_DETECT="$AUTO_DETECT_TRACKPADS" \
WC_EXCLUDED_KEYWORDS="$EXCLUDED_KW_STR" \
WC_AUDIO_BUFFER="$AUDIO_BUFFER_SIZE" \
WC_AUDIO_RATE="$AUDIO_SAMPLE_RATE" \
WC_MIX_CHANNELS="$AUDIO_MIX_CHANNELS" \
WC_POLL_INTERVAL="$HOTPLUG_POLL_SECONDS" \
WC_DEBUG="$DEBUG_MODE" \
PIPEWIRE_LATENCY="${AUDIO_BUFFER_SIZE}/${AUDIO_SAMPLE_RATE}" \
"$PYTHON_BIN" -OO -B "$RUNNER_SCRIPT" "$CONFIG_DIR" "$AUDIO_PACK" &
RUNNER_CHILD_PID="$!"

write_pid_file "$RUNNER_CHILD_PID"
update_state "True"
release_lock

if ! $INTERACTIVE; then
    sleep 0.2
    if kill -0 "$RUNNER_CHILD_PID" 2>/dev/null; then
        notify_user "Enabled (${AUDIO_PACK})"
    fi
fi

set +e
wait "$RUNNER_CHILD_PID"
runner_status=$?
set -e

clear_pid_file_if_owned_by_child

if (( runner_status == 0 )); then
    printf "\n%b[INFO]%b WayClick stopped.\n" "${C_BLUE}" "${C_RESET}"
else
    printf "\n%b[ERROR]%b WayClick exited with status %d.\n" \
        "${C_RED}" "${C_RESET}" "$runner_status"
fi

exit "$runner_status"
