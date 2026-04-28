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
#    → Capability check catches unnamed touchpads (ITE5570, ELAN, etc.)
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
        printf "Usage: %s [--setup|--reset]\n\n" "$(basename "$0")"
        printf "  (no args)  Start or stop WayClick (toggle)\n"
        printf "  --setup    Install dependencies and build the environment only\n"
        printf "  --reset    Stop WayClick and delete the environment\n"
        exit 0
        ;;
    "") ;;
    *)
        printf "Unknown option: %s\nRun '%s --help' for usage.\n" "$1" "$(basename "$0")"
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

# Auto-detect touchpads by hardware capabilities (multitouch axes, finger tools).
# Catches unnamed/obscure touchpads (e.g. ITE5570, ELAN) that don't match any keyword.
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

# ╔════════════════════════════════════════════════════════════════════════════╗
# ║  INTERNAL CONFIGURATION — Change only if you know what you're doing      ║
# ╚════════════════════════════════════════════════════════════════════════════╝

readonly APP_NAME="wayclick"
readonly BASE_DIR="$HOME/contained_apps/uv/$APP_NAME"
readonly VENV_DIR="$BASE_DIR/.venv"
readonly PYTHON_BIN="$VENV_DIR/bin/python"
readonly RUNNER_SCRIPT="$BASE_DIR/runner.py"
readonly CONFIG_DIR="$HOME/.config/wayclick"
readonly STATE_FILE="$HOME/.config/dusky/settings/wayclick"

# --- ANSI COLORS ---
readonly C_RED=$'\033[1;31m'
readonly C_GREEN=$'\033[1;32m'
readonly C_BLUE=$'\033[1;34m'
readonly C_CYAN=$'\033[1;36m'
readonly C_YELLOW=$'\033[1;33m'
readonly C_DIM=$'\033[2m'
readonly C_RESET=$'\033[0m'

# --- UTILITY FUNCTIONS ---

update_state() {
    local status="$1"
    local dir state_tmp
    dir="${STATE_FILE%/*}"
    state_tmp="${STATE_FILE}.tmp.$$"

    mkdir -p "$dir" 2>/dev/null || true

    printf '%s\n' "$status" > "$state_tmp" && mv -f "$state_tmp" "$STATE_FILE"
}

cleanup() {
    tput cnorm 2>/dev/null || true
    if [[ "${RUN_MODE:-run}" != "setup" ]] && [[ -n "${STATE_FILE:-}" ]]; then
        update_state "False"
    fi
}

notify_user() {
    command -v notify-send >/dev/null 2>&1 && \
        notify-send -t 2000 --app-name="WayClick" "WayClick Elite" "$1"
}

trap cleanup EXIT INT TERM

# --- 0. ROOT CHECK ---
if (( EUID == 0 )); then
    printf "%b[CRITICAL]%b Do not run this script as root.\n" "${C_RED}" "${C_RESET}"
    exit 1
fi

# --- 1. RESET MODE ---
if [[ "$RUN_MODE" == "reset" ]]; then
    if pgrep -u "$USER" -f "$RUNNER_SCRIPT" >/dev/null 2>&1; then
        printf "%b[RESET]%b Stopping running instance...\n" "${C_YELLOW}" "${C_RESET}"
        notify_user "Disabled"

        pkill -TERM -u "$USER" -f "$RUNNER_SCRIPT" 2>/dev/null || true

        wait_count=0
        while pgrep -u "$USER" -f "$RUNNER_SCRIPT" >/dev/null 2>&1 && (( wait_count++ < 20 )); do
            sleep 0.1
        done

        pkill -KILL -u "$USER" -f "$RUNNER_SCRIPT" 2>/dev/null || true
    fi

    if [[ -d "$VENV_DIR" ]]; then
        rm -rf "$VENV_DIR"
        rm -f "$BASE_DIR"/.build_marker_*
        rm -f "$RUNNER_SCRIPT"
        printf "%b[RESET]%b Environment deleted successfully.\n" "${C_GREEN}" "${C_RESET}"
    else
        printf "%b[RESET]%b Nothing to clean (environment not found).\n" "${C_BLUE}" "${C_RESET}"
    fi
    exit 0
fi

# --- 2. TOGGLE (run mode only) ---
if [[ "$RUN_MODE" == "run" ]]; then
    if pgrep -u "$USER" -f "$RUNNER_SCRIPT" >/dev/null 2>&1; then
        printf "%b[TOGGLE]%b Stopping active instance...\n" "${C_YELLOW}" "${C_RESET}"
        notify_user "Disabled"

        pkill -TERM -u "$USER" -f "$RUNNER_SCRIPT" 2>/dev/null || true

        wait_count=0
        while pgrep -u "$USER" -f "$RUNNER_SCRIPT" >/dev/null 2>&1 && (( wait_count++ < 20 )); do
            sleep 0.1
        done

        pkill -KILL -u "$USER" -f "$RUNNER_SCRIPT" 2>/dev/null || true
        exit 0
    fi
fi

# --- 3. INTERACTIVE DETECTION ---
[[ -t 0 ]] && INTERACTIVE=true || INTERACTIVE=false

# --- 4. DEPENDENCY CHECK ---
declare -a NEEDED_DEPS=()
AUDIO_PKGS_INSTALLED=false

command -v uv >/dev/null 2>&1          || NEEDED_DEPS+=("uv")
command -v notify-send >/dev/null 2>&1 || NEEDED_DEPS+=("libnotify")

# Runtime audio stack (always required regardless of build marker)
audio_deps=("pipewire" "pipewire-audio" "pipewire-pulse" "wireplumber")
for dep in "${audio_deps[@]}"; do
    if ! pacman -Qq "$dep" >/dev/null 2>&1; then
        NEEDED_DEPS+=("$dep")
        AUDIO_PKGS_INSTALLED=true
    fi
done

# Build-time deps (only needed if native compilation hasn't been done yet)
if [[ ! -f "$BASE_DIR/.build_marker_v10" ]]; then
    command -v gcc >/dev/null 2>&1 || NEEDED_DEPS+=("gcc")

    # SDL headers/libs for pygame-ce, portmidi for MIDI, freetype2 for fonts,
    # pkgconf so Meson finds libraries via .pc files, libuv for uvloop.
    build_deps=("sdl2" "sdl2_mixer" "sdl2_image" "sdl2_ttf" "portmidi" "freetype2" "pkgconf" "libuv")
    for dep in "${build_deps[@]}"; do
        pacman -Qq "$dep" >/dev/null 2>&1 || NEEDED_DEPS+=("$dep")
    done
fi

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

# --- 5. PIPEWIRE SERVICE ACTIVATION ---
# On fresh installs, PipeWire services may be installed but not started.
# If we just installed audio packages, PipeWire needs a restart to discover
# the new ALSA SPA plugins and create audio device nodes.
if $AUDIO_PKGS_INSTALLED || ! systemctl --user is-active pipewire.service >/dev/null 2>&1; then
    printf "%b[AUDIO]%b Activating PipeWire audio services...\n" "${C_BLUE}" "${C_RESET}"
    systemctl --user enable pipewire.service pipewire-pulse.service wireplumber.service 2>/dev/null || true
    systemctl --user restart pipewire.service pipewire-pulse.service wireplumber.service 2>/dev/null || true
    sleep 1
fi

# --- 6a. CONFIG FILE CHECK ---
if [[ ! -f "${CONFIG_DIR}/${AUDIO_PACK}/config.json" ]]; then
    if $INTERACTIVE; then
        while [[ ! -f "${CONFIG_DIR}/${AUDIO_PACK}/config.json" ]]; do
            printf "\n%b[ACTION REQUIRED]%b Missing config.json in: %s\n" \
                "${C_YELLOW}" "${C_RESET}" "${CONFIG_DIR}/${AUDIO_PACK}"
            mkdir -p "${CONFIG_DIR}/${AUDIO_PACK}" 2>/dev/null || true
            printf "       Please ensure 'config.json' exists in this folder.\n"
            printf "       %bPress Enter to re-scan...%b" "${C_DIM}" "${C_RESET}"
            read -r
        done
        printf "%b[CHECK]%b Configuration found.\n" "${C_GREEN}" "${C_RESET}"
    else
        notify_user "Missing config.json in ~/.config/wayclick/${AUDIO_PACK}. Run in terminal."
        exit 1
    fi
fi

# --- 6b. AUDIO PACK CHECK ---
if [[ ! -d "${CONFIG_DIR}/${AUDIO_PACK}" ]]; then
    if $INTERACTIVE; then
        printf "\n%b[ERROR]%b Audio pack '%b%s%b' not found in: %s\n" \
            "${C_RED}" "${C_RESET}" "${C_CYAN}" "$AUDIO_PACK" "${C_RESET}" "${CONFIG_DIR}"

        mapfile -t available < <(find "$CONFIG_DIR" -mindepth 1 -maxdepth 1 -type d -printf '%f\n' 2>/dev/null | sort)

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

# --- ENVIRONMENT SETUP ---

mkdir -p "$BASE_DIR" 2>/dev/null || true

if [[ ! -d "$VENV_DIR" ]]; then
    if ! $INTERACTIVE; then
        notify_user "Environment not built! Run in terminal once to initialize."
        exit 1
    fi
    printf "%b[BUILD]%b Initializing UV environment...\n" "${C_BLUE}" "${C_RESET}"
    uv venv "$VENV_DIR" --python 3.14 --quiet
fi

MARKER_FILE="$BASE_DIR/.build_marker_v10"

if [[ ! -f "$MARKER_FILE" ]]; then
    if ! $INTERACTIVE; then
        notify_user "First run setup required! Run in terminal to build native extensions."
        exit 1
    fi

    printf "%b[BUILD]%b Compiling dependencies with NATIVE CPU FLAGS (LTO / march=native)...\n" \
        "${C_YELLOW}" "${C_RESET}"

    export CFLAGS="-march=native -mtune=native -O3 -pipe -fno-plt -fno-semantic-interposition -fno-math-errno -fno-trapping-math -flto=auto -ffat-lto-objects -ffp-contract=fast -DNDEBUG"
    export CXXFLAGS="$CFLAGS"
    export LDFLAGS="-Wl,-O2,--sort-common,--as-needed,-z,now,--relax -flto=auto"

    # --no-binary targets ONLY our runtime libraries (evdev, pygame-ce).
    # Build tools (cmake, ninja, scikit-build-core) use pre-built wheels.
    # This avoids needing 'make' on fresh installs while still compiling
    # the hot-path C extensions with native CPU flags.
    uv pip install --python "$PYTHON_BIN" \
        --no-binary evdev --no-binary pygame-ce \
        --no-cache \
        --compile-bytecode \
        evdev pygame-ce

    printf "%b[BUILD]%b Attempting uvloop (optional, faster event loop)...\n" "${C_BLUE}" "${C_RESET}"
    uv pip install --python "$PYTHON_BIN" \
        --no-binary uvloop \
        --no-cache \
        --compile-bytecode \
        uvloop 2>/dev/null \
        && printf "%b[SUCCESS]%b uvloop installed.\n" "${C_GREEN}" "${C_RESET}" \
        || printf "%b[INFO]%b uvloop skipped (optional). Standard asyncio will be used.\n" "${C_YELLOW}" "${C_RESET}"

    touch "$MARKER_FILE"
    printf "%b[SUCCESS]%b Native build complete.\n" "${C_GREEN}" "${C_RESET}"
fi

# --- PYTHON RUNNER GENERATION ---
cat > "$RUNNER_SCRIPT" << 'PYTHON_EOF'
import asyncio
import gc
import os
import sys
import signal
import random
import json

# === FAST EVENT LOOP ===
try:
    import uvloop
    _UVLOOP = True
except ImportError:
    _UVLOOP = False

# === ENVIRONMENT ===
os.environ['PYGAME_HIDE_SUPPORT_PROMPT'] = '1'
os.environ['SDL_AUDIODRIVER'] = 'pipewire,pulseaudio,alsa'

import pygame
import evdev

C_GREEN  = "\033[1;32m"
C_YELLOW = "\033[1;33m"
C_BLUE   = "\033[1;34m"
C_RED    = "\033[1;31m"
C_DIM    = "\033[2m"
C_RESET  = "\033[0m"

# === CONFIGURATION FROM ENVIRONMENT / ARGS ===
CONFIG_DIR  = sys.argv[1]
PACK_NAME   = sys.argv[2]
ASSET_DIR   = os.path.join(CONFIG_DIR, PACK_NAME)
CONFIG_FILE = os.path.join(ASSET_DIR, "config.json")

ENABLE_TRACKPADS = os.environ.get('ENABLE_TRACKPADS', 'false').lower() == 'true'
AUTO_DETECT      = os.environ.get('WC_AUTO_DETECT', 'true').lower() == 'true'
DEBUG            = os.environ.get('WC_DEBUG', 'false').lower() == 'true'
BUFFER_SIZE      = int(os.environ.get('WC_AUDIO_BUFFER', '512'))
SAMPLE_RATE      = int(os.environ.get('WC_AUDIO_RATE', '48000'))
MIX_CHANNELS     = int(os.environ.get('WC_MIX_CHANNELS', '16'))
POLL_INTERVAL    = float(os.environ.get('WC_POLL_INTERVAL', '1.0'))

raw_keywords = os.environ.get('WC_EXCLUDED_KEYWORDS', 'touchpad,trackpad')
EXCLUDED_KEYWORDS = [x.strip().lower() for x in raw_keywords.split(',') if x.strip()]

# === TOUCHPAD CAPABILITY CONSTANTS ===
_EV_ABS = 3
_ABS_MT_POSITION_X = 0x35
_BTN_TOOL_FINGER = 0x145

# === AUDIO INIT ===
try:
    pygame.mixer.pre_init(frequency=SAMPLE_RATE, size=-16, channels=2, buffer=BUFFER_SIZE)
    pygame.mixer.init()
    pygame.mixer.set_num_channels(MIX_CHANNELS)
except pygame.error as e:
    sys.exit(f"{C_RED}[AUDIO ERROR]{C_RESET} {e}")

latency_ms = BUFFER_SIZE / SAMPLE_RATE * 1000
print(f"{C_BLUE}[AUDIO]{C_RESET} Buffer={BUFFER_SIZE} samples (~{latency_ms:.1f}ms) | "
      f"Rate={SAMPLE_RATE}Hz | Channels={MIX_CHANNELS}")

# === DISABLE GARBAGE COLLECTOR ===
gc.disable()

# === CONFIG LOADING ===
print(f"{C_BLUE}[INFO]{C_RESET}  Config: {CONFIG_FILE}")
print(f"{C_BLUE}[INFO]{C_RESET}  Pack:   {ASSET_DIR}")

try:
    with open(CONFIG_FILE, 'r') as f:
        config_data = json.load(f)
        RAW_KEY_MAP = {int(k): v for k, v in config_data.get("mappings", {}).items()}
        DEFAULTS = config_data.get("defaults", [])
except Exception as e:
    sys.exit(f"{C_RED}[CONFIG ERROR]{C_RESET} Failed to load {CONFIG_FILE}: {e}")

# === SOUND LOADING ===
SOUND_FILES = set(RAW_KEY_MAP.values()) | set(DEFAULTS)
SOUNDS = {}

for filename in SOUND_FILES:
    path = os.path.join(ASSET_DIR, filename)
    if os.path.exists(path):
        try:
            snd = pygame.mixer.Sound(path)
            snd.set_volume(1.0)
            SOUNDS[filename] = snd
        except pygame.error:
            print(f"{C_YELLOW}[WARN]{C_RESET} Failed to load wav: {filename}")
    else:
        print(f"{C_YELLOW}[WARN]{C_RESET} File not found in pack: {filename}")

if not SOUNDS:
    sys.exit(f"ERROR: No sounds loaded! Check config.json mappings and .wav files in '{PACK_NAME}'.")

print(f"{C_BLUE}[INFO]{C_RESET}  Loaded {len(SOUNDS)} sound(s) from pack '{PACK_NAME}'")

# === PERFORMANCE: FLAT ARRAY CACHE ===
MAX_KEYCODE = 1024
SOUND_CACHE = [None] * MAX_KEYCODE
DEFAULT_SOUND_OBJS = tuple(SOUNDS[f] for f in DEFAULTS if f in SOUNDS)

for code, filename in RAW_KEY_MAP.items():
    if code < MAX_KEYCODE and filename in SOUNDS:
        SOUND_CACHE[code] = SOUNDS[filename]

# === HOT PATH PRE-BINDING ===
_random_choice = random.choice
_sound_cache   = SOUND_CACHE
_max_keycode   = MAX_KEYCODE
_defaults      = DEFAULT_SOUND_OBJS
_has_defaults  = bool(DEFAULT_SOUND_OBJS)

# === PLAY FUNCTION ===
if DEBUG:
    import time
    _perf = time.perf_counter_ns

    def play_sound(code):
        t0 = _perf()
        sound = _sound_cache[code] if code < _max_keycode else None
        if sound is not None:
            sound.play()
        elif _has_defaults:
            _random_choice(_defaults).play()
        elapsed_us = (_perf() - t0) / 1000
        print(f"  \u23f1 {elapsed_us:.1f}\u00b5s [code={code}]")
else:
    def play_sound(code):
        sound = _sound_cache[code] if code < _max_keycode else None
        if sound is not None:
            sound.play()
        elif _has_defaults:
            _random_choice(_defaults).play()

# === DEVICE READER ===
async def read_device(dev, stop_event):
    _play = play_sound
    _is_stopped = stop_event.is_set

    print(f"{C_GREEN}[+] Connected:{C_RESET} {dev.name} {C_DIM}({dev.path}){C_RESET}")
    try:
        async for event in dev.async_read_loop():
            if _is_stopped():
                break
            if event.type == 1 and event.value == 1:
                _play(event.code)
    except (OSError, IOError):
        print(f"{C_YELLOW}[-] Disconnected:{C_RESET} {dev.path}")
    except asyncio.CancelledError:
        pass
    finally:
        try:
            dev.close()
        except (OSError, IOError):
            pass

# === MAIN LOOP ===
async def main():
    loop_type = "uvloop (native)" if _UVLOOP else "asyncio (standard)"
    print(f"{C_BLUE}[CORE]{C_RESET}  Engine started | Event loop: {loop_type}")

    filter_mode = "disabled (all devices play sounds)" if ENABLE_TRACKPADS else \
                  f"keyword blacklist ({len(EXCLUDED_KEYWORDS)} entries)" + \
                  (" + auto-detect" if AUTO_DETECT else " only")
    print(f"{C_BLUE}[CORE]{C_RESET}  Filtering: {filter_mode}")
    print(f"{C_BLUE}[CORE]{C_RESET}  Monitoring devices (poll: {POLL_INTERVAL}s)...")

    stop = asyncio.Event()

    def _request_shutdown(signum, frame):
        stop.set()

    signal.signal(signal.SIGINT, _request_shutdown)
    signal.signal(signal.SIGTERM, _request_shutdown)

    monitored_tasks = {}
    skipped_paths = set()
    _list_devices = evdev.list_devices

    while not stop.is_set():
        try:
            all_paths = _list_devices()
            current_set = set(all_paths)

            skipped_paths &= current_set

            for path in all_paths:
                if path in monitored_tasks or path in skipped_paths:
                    continue

                try:
                    dev = evdev.InputDevice(path)
                    caps = dev.capabilities(absinfo=False)

                    if not ENABLE_TRACKPADS:
                        name_lower = dev.name.lower()
                        keyword_match = any(k in name_lower for k in EXCLUDED_KEYWORDS)

                        if keyword_match:
                            print(f"{C_DIM}[~] Skipped: {dev.name} ({dev.path}) [keyword]{C_RESET}")
                            dev.close()
                            skipped_paths.add(path)
                            continue

                        if AUTO_DETECT:
                            abs_codes = caps.get(_EV_ABS, [])
                            key_codes = caps.get(1, [])
                            has_mt = _ABS_MT_POSITION_X in abs_codes
                            has_finger = _BTN_TOOL_FINGER in key_codes

                            if has_mt or has_finger:
                                print(f"{C_DIM}[~] Skipped: {dev.name} ({dev.path}) [auto-detected touchpad]{C_RESET}")
                                dev.close()
                                skipped_paths.add(path)
                                continue

                    if 1 in caps:
                        task = asyncio.create_task(read_device(dev, stop))
                        monitored_tasks[path] = task
                    else:
                        dev.close()
                        skipped_paths.add(path)
                except (OSError, IOError):
                    continue

        except Exception as e:
            print(f"Discovery Loop Error: {e}")

        dead_paths = [p for p, t in monitored_tasks.items() if t.done()]
        for p in dead_paths:
            del monitored_tasks[p]

        try:
            await asyncio.wait_for(stop.wait(), timeout=POLL_INTERVAL)
        except asyncio.TimeoutError:
            continue

    print("\nStopping...")
    for t in monitored_tasks.values():
        t.cancel()
    if monitored_tasks:
        await asyncio.gather(*monitored_tasks.values(), return_exceptions=True)
    pygame.mixer.quit()

if __name__ == "__main__":
    try:
        if _UVLOOP:
            uvloop.run(main())
        else:
            asyncio.run(main())
    except KeyboardInterrupt:
        pass
PYTHON_EOF

# --- SETUP MODE EXIT ---
if [[ "$RUN_MODE" == "setup" ]]; then
    printf "\n%b[SETUP]%b Setup complete! Run '%b%s%b' to start WayClick.\n" \
        "${C_GREEN}" "${C_RESET}" "${C_CYAN}" "$(basename "$0")" "${C_RESET}"

    # Non-blocking group reminder (setup doesn't need it, but runtime does)
    if ! id -nG "$USER" | grep -qw input; then
        printf "%b[NOTE]%b  User '%s' is not in the 'input' group (required to run).\n" \
            "${C_YELLOW}" "${C_RESET}" "$USER"
        printf "        Run: %bsudo usermod -aG input %s%b (then logout/login)\n" \
            "${C_CYAN}" "$USER" "${C_RESET}"
    fi
    exit 0
fi

# --- 7. GROUP PERMISSION CHECK (run mode only — after build so first run completes setup) ---
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
        else
            exit 1
        fi
    else
        notify_user "Permission error: User not in 'input' group. Run in terminal."
        exit 1
    fi
fi

# --- EXECUTION ---
printf "%b[RUN]%b Starting engine (pack: %b%s%b | buffer: %s samples)...\n" \
    "${C_BLUE}" "${C_RESET}" "${C_CYAN}" "$AUDIO_PACK" "${C_RESET}" "$AUDIO_BUFFER_SIZE"

$INTERACTIVE || notify_user "Enabled (${AUDIO_PACK})"

update_state "True"

EXCLUDED_KW_STR=$(IFS=, ; echo "${EXCLUDED_KEYWORDS[*]}")

ENABLE_TRACKPADS="$ENABLE_TRACKPAD_SOUNDS" \
WC_AUTO_DETECT="$AUTO_DETECT_TRACKPADS" \
WC_EXCLUDED_KEYWORDS="$EXCLUDED_KW_STR" \
WC_AUDIO_BUFFER="$AUDIO_BUFFER_SIZE" \
WC_AUDIO_RATE="$AUDIO_SAMPLE_RATE" \
WC_MIX_CHANNELS="$AUDIO_MIX_CHANNELS" \
WC_POLL_INTERVAL="$HOTPLUG_POLL_SECONDS" \
WC_DEBUG="$DEBUG_MODE" \
PIPEWIRE_LATENCY="${AUDIO_BUFFER_SIZE}/${AUDIO_SAMPLE_RATE}" \
"$PYTHON_BIN" -OO -B "$RUNNER_SCRIPT" "$CONFIG_DIR" "$AUDIO_PACK"

printf "\n%b[INFO]%b WayClick stopped.\n" "${C_BLUE}" "${C_RESET}"
