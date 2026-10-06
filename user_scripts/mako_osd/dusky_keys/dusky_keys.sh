#!/usr/bin/env bash
# Dusky Keys: opt-in Hyprland key/mouse OSD, native Python 3.15 and UV.
set -euo pipefail
shopt -s inherit_errexit
(( $# <= 1 )) || { printf 'Expected one operation at most.\n' >&2; exit 2; }
RUN_MODE=run
case ${1:-} in
    --setup) RUN_MODE=setup ;;
    --reset) RUN_MODE=reset ;;
    --config) RUN_MODE=config ;;
    --restart) RUN_MODE=restart ;;
    --help|-h) printf 'Usage: %s [--setup|--reset|--config|--restart]\nNo argument toggles the visualizer.\n' "${0##*/}"; exit 0 ;;
    '') ;;
    *) printf 'Unknown operation: %s\n' "$1" >&2; exit 2 ;;
esac
script_path=${BASH_SOURCE[0]}
[[ $script_path == /* ]] || script_path=$PWD/$script_path
[[ ! -L $script_path ]] || script_path=$(readlink -f -- "$script_path")
readonly SCRIPT_PATH="$script_path"
readonly BASE_DIR="$HOME/contained_apps/uv/dusky_keys"
readonly VENV_DIR="$BASE_DIR/.venv"
readonly PYTHON_BIN="$VENV_DIR/bin/python"
readonly RUNNER_SCRIPT="$BASE_DIR/runner.py"
readonly PID_FILE="$BASE_DIR/dusky_keys.pid"
readonly READY_FILE="$BASE_DIR/dusky_keys.ready.$BASHPID"
readonly MARKER_FILE="$BASE_DIR/.build_marker_v3"
readonly USER_CONFIG_DIR="$HOME/.config/dusky/settings/dusky_keys"
readonly USER_CONFIG_FILE="$USER_CONFIG_DIR/config.toml"
CHILD_PID='' CHILD_START='' LOCK_FD='' RUNNER_TMP=''
LOCK_HELD=false
LEGACY_STOPPED=false
readonly C_GREEN=$'\033[1;32m' C_RESET=$'\033[0m'
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
            exec timeout --kill-after=1 3 notify-send -t 2000 --app-name=dusky-keys "Dusky Keys" "$1"
        ) >/dev/null 2>&1 &
    fi
}
acquire_lock() {
    if ! $LOCK_HELD; then
        local pid start
        exec {LOCK_FD}> "$BASE_DIR/dusky_keys.lock"
        # The previous launcher held this lock throughout its lifetime and
        # wrote a single PID. Stop that verified runner before taking over.
        if ! flock -n "$LOCK_FD"; then
            if [[ -r $PID_FILE ]] && read -r pid start < "$PID_FILE" \
                && [[ $pid =~ ^[1-9][0-9]*$ && -z $start ]] && proc_identity "$pid"; then
                start=$REPLY
                if runner_alive "$pid" "$start"; then
                    [[ $RUN_MODE != setup ]] || fail 'Stop Dusky Keys before rebuilding its environment.'
                    stop_runner "$pid" "$start"
                    LEGACY_STOPPED=true
                fi
            fi
        fi
        flock -w 30 "$LOCK_FD" || fail 'Another Dusky Keys operation is still running.'
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
    done < <(pgrep -u "$EUID" -f '[/]dusky_keys/runner[.]py' || true)
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
        rm -f -- "$PID_FILE" "$BASE_DIR"/dusky_keys.ready.*
    fi
    release_lock
    exit "$rc"
}
deploy_config() {
    mkdir -p "$USER_CONFIG_DIR"
    if [[ ! -f "$USER_CONFIG_FILE" ]]; then
        cat > "$USER_CONFIG_FILE" << 'TOML_EOF'
# ==============================================================================
# DUSKY KEYS CONFIGURATION
# Location: ~/.config/dusky/settings/dusky_keys/config.toml
# ==============================================================================

[display]
# Maximum number of key items/chords in the OSD notification buffer.
buffer_size = 10

# Display timeout in seconds before clearing the OSD notification.
display_timeout = 2.5

# Use compact symbols for modifier keys and special keys (❖, ⌃, ⌥, ⇧, ⇥, ⏎, ⌫, Esc).
compact_symbols = true

# Put escaped bold text in the notification body. Mako's format needs %b.
use_pango_markup = false

# Delimiter between sequential keystroke items in the buffer.
separator = " "

[chording]
# Group held modifier keys + target key into unified chords (e.g. ❖S or ⌃C).
enable_chording = true

# Suppress emitting pure modifier keys when pressed & held down.
# Pure modifier symbol is emitted ONLY if tapped & released alone without pressing another key.
suppress_pure_modifiers = true

[mouse]
# Enable capturing mouse button clicks (Left, Right, Middle, Back, Forward).
enable_mouse = false

# Custom mouse button symbols (used in compact mode)
left_click = "LMB"
right_click = "RMB"
middle_click = "MMB"
side_click = "Back"
extra_click = "Fwd"

[notification]
app_name = "dusky-keys"
sync_id = "dusky-keys-sync"
urgency = "low"

[symbols]
super = "❖"
ctrl = "⌃"
alt = "⌥"
shift = "⇧"
tab = "⇥"
enter = "⏎"
backspace = "⌫"
delete = "⌦"
escape = "⎋"
space = "␣"
caps_lock = "⇪"
up = "↑"
down = "↓"
left = "←"
right = "→"
page_up = "PgUp"
page_down = "PgDn"
home = "Home"
end = "End"
TOML_EOF
        printf "%b[CONFIG]%b Deployed default configuration to %s\n" "${C_GREEN}" "${C_RESET}" "$USER_CONFIG_FILE"
    fi
}

if [[ $RUN_MODE == config ]]; then
    deploy_config
    if [[ -n ${EDITOR:-} ]]; then
        read -ra editor_command <<< "$EDITOR"
        exec "${editor_command[@]}" "$USER_CONFIG_FILE"
    fi
    printf 'Config file: %s\n' "$USER_CONFIG_FILE"
    exit 0
fi
(( EUID != 0 )) || fail 'Run Dusky Keys as the desktop user.'
command -v flock >/dev/null || fail 'flock (util-linux) is required.'
mkdir -p "$BASE_DIR"
acquire_lock
trap cleanup EXIT
trap 'exit 130' INT
trap 'exit 143' TERM
trap 'exit 129' HUP

if $LEGACY_STOPPED && [[ $RUN_MODE == run ]]; then
    notify_user Disabled
    exit 0
fi
mapfile -t records < <(find_runners)
if (( ${#records[@]} )); then
    [[ $RUN_MODE != setup ]] || fail 'Stop Dusky Keys before rebuilding its environment.'
    for record in "${records[@]}"; do
        read -r pid start <<< "$record"
        stop_runner "$pid" "$start"
    done
    rm -f -- "$PID_FILE"
    notify_user Disabled
    [[ $RUN_MODE == reset || $RUN_MODE == restart ]] || exit 0
fi
if [[ $RUN_MODE == reset ]]; then
    rm -rf -- "$VENV_DIR"
    rm -f -- "$RUNNER_SCRIPT" "$PID_FILE" "$BASE_DIR"/.build_marker_* "$BASE_DIR"/dusky_keys.ready.*
    printf '[RESET] Environment removed.\n'
    exit 0
fi

deploy_config
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
    local -a deps=(uv gcc linux-api-headers mako libnotify shadow)
    local -a missing=()
    local -A installed=()
    select_python || deps+=(python)
    command -v pacman >/dev/null || fail 'Dusky Keys setup requires Arch Linux/pacman.'
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
    "$PYTHON_BIN" - <<'CHECK' >/dev/null 2>&1
import sys
if sys.version_info < (3, 15, 0, 'candidate', 3):
    sys.exit(1)
import evdev
from dbus_fast.aio import MessageBus
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
        # Use portable compiler defaults, so cached extensions remain reusable.
        uv pip install --python "$PYTHON_BIN" --link-mode=copy --compile-bytecode \
            'evdev>=2.0' 'dbus-fast>=5.2'
        runtime_ready || fail 'Python runtime validation failed after setup.'
    fi
}
current_input_access() {
    # One readable virtual device does not imply access to physical keyboards.
    [[ " $(id -nG) " == *' input '* ]]
}
open_setup_terminal() {
    command -v xdg-terminal-exec >/dev/null || fail 'Run Dusky Keys in a terminal once to install dependencies.'
    release_lock
    trap - EXIT INT TERM HUP
    exec xdg-terminal-exec -- bash "$SCRIPT_PATH"
}
prepare_input_access() {
    current_input_access && return 0
    # An existing venv does not mean this desktop session has the input group.
    if [[ $RUN_MODE != setup && ! -t 0 ]]; then
        open_setup_terminal
    fi
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
    [[ ${DK_REPAIR_ATTEMPT:-0} != 1 ]] || fail 'Automatic repair failed; run --setup in a terminal for details.'
    printf '[REPAIR] Rebuilding the environment after a startup failure.\n'
    rm -f -- "$MARKER_FILE" "$PID_FILE" "$READY_FILE"
    CHILD_PID='' CHILD_START=''
    release_lock
    trap - EXIT INT TERM HUP
    exec env DK_REPAIR_ATTEMPT=1 bash "$SCRIPT_PATH"
}

if [[ $RUN_MODE == setup || ! -f $MARKER_FILE || ! -x $PYTHON_BIN ]]; then
    if [[ $RUN_MODE != setup && ! -t 0 ]]; then
        # Hyprland's first click opens setup in the preferred Wayland terminal.
        open_setup_terminal
    fi
    install_system_deps
    prepare_environment
    touch "$MARKER_FILE"
    prepare_input_access
fi

RUNNER_TMP="$BASE_DIR/.runner.$BASHPID.tmp"
cat > "$RUNNER_TMP" << 'PYTHON_EOF'
import asyncio
from collections import deque
lazy import html
import math
import os
from pathlib import Path
import signal
import sys
import tomllib

from dbus_fast import Message, MessageType, Variant
from dbus_fast.aio import MessageBus
from evdev import InputDevice, ecodes, list_devices

CONFIG_PATH = Path.home() / '.config/dusky/settings/dusky_keys/config.toml'
READY_FILE = os.environ.get('DK_READY_FILE', '')


def load_config() -> dict:
    try:
        with CONFIG_PATH.open('rb') as stream:
            config = tomllib.load(stream)
        for section in ('display', 'chording', 'mouse', 'notification', 'symbols'):
            if not isinstance(config.get(section, {}), dict):
                raise ValueError(f'{section} must be a TOML table')
        return config
    except (OSError, ValueError) as exc:
        sys.exit(f'[CONFIG ERROR] {CONFIG_PATH}: {exc}')


CONFIG = load_config()
CFG_DISP = CONFIG.get('display', {})
CFG_CHORD = CONFIG.get('chording', {})
CFG_MOUSE = CONFIG.get('mouse', {})
CFG_NOTIF = CONFIG.get('notification', {})
CFG_SYM = CONFIG.get('symbols', {})


def setting(section: dict, key: str, default):
    value = section.get(key, default)
    valid = type(value) is type(default)
    if type(default) is float:
        valid = type(value) in (float, int) and math.isfinite(value)
    if isinstance(value, str) and '\0' in value:
        valid = False
    if not valid:
        sys.exit(f'[CONFIG ERROR] Invalid type/value for {key}')
    return value


BUFFER_SIZE = setting(CFG_DISP, 'buffer_size', 10)
DISPLAY_TIMEOUT = setting(CFG_DISP, 'display_timeout', 2.5)
if not 1 <= BUFFER_SIZE <= 1000 or not 0 < DISPLAY_TIMEOUT <= 86400:
    sys.exit('[CONFIG ERROR] buffer_size must be 1..1000; display_timeout must be >0 and <=86400')
COMPACT_SYMBOLS = setting(CFG_DISP, 'compact_symbols', True)
PANGO_MARKUP = setting(CFG_DISP, 'use_pango_markup', False)
SEPARATOR = setting(CFG_DISP, 'separator', ' ')
ENABLE_CHORDING = setting(CFG_CHORD, 'enable_chording', True)
SUPPRESS_PURE_MODS = setting(CFG_CHORD, 'suppress_pure_modifiers', True)
ENABLE_MOUSE = setting(CFG_MOUSE, 'enable_mouse', False)
APP_NAME = setting(CFG_NOTIF, 'app_name', 'dusky-keys')
SYNC_ID = setting(CFG_NOTIF, 'sync_id', 'dusky-keys-sync')
URGENCY = setting(CFG_NOTIF, 'urgency', 'low')
if URGENCY not in ('low', 'normal', 'critical'):
    sys.exit('[CONFIG ERROR] urgency must be low, normal or critical')
# Resolve the optional lazy import before input arrives.
if PANGO_MARKUP:
    html.escape('')


def get_sym(key: str, default_compact: str, default_full: str) -> str:
    value = setting(CFG_SYM, key, default_compact)
    return value if COMPACT_SYMBOLS else default_full

SYM_SUPER = get_sym("super", "❖", "Super")
SYM_CTRL = get_sym("ctrl", "⌃", "Ctrl")
SYM_ALT = get_sym("alt", "⌥", "Alt")
SYM_SHIFT = get_sym("shift", "⇧", "Shift")
SPACE_SYMBOL = get_sym('space', '␣', 'Space')

MOD_MAP = {
    ecodes.KEY_LEFTMETA: ("super", SYM_SUPER),
    ecodes.KEY_RIGHTMETA: ("super", SYM_SUPER),
    ecodes.KEY_LEFTCTRL: ("ctrl", SYM_CTRL),
    ecodes.KEY_RIGHTCTRL: ("ctrl", SYM_CTRL),
    ecodes.KEY_LEFTALT: ("alt", SYM_ALT),
    ecodes.KEY_RIGHTALT: ("alt", SYM_ALT),
    ecodes.KEY_LEFTSHIFT: ("shift", SYM_SHIFT),
    ecodes.KEY_RIGHTSHIFT: ("shift", SYM_SHIFT),
}

KEYMAP = {
    ecodes.KEY_A: ('a', 'A'), ecodes.KEY_B: ('b', 'B'), ecodes.KEY_C: ('c', 'C'),
    ecodes.KEY_D: ('d', 'D'), ecodes.KEY_E: ('e', 'E'), ecodes.KEY_F: ('f', 'F'),
    ecodes.KEY_G: ('g', 'G'), ecodes.KEY_H: ('h', 'H'), ecodes.KEY_I: ('i', 'I'),
    ecodes.KEY_J: ('j', 'J'), ecodes.KEY_K: ('k', 'K'), ecodes.KEY_L: ('l', 'L'),
    ecodes.KEY_M: ('m', 'M'), ecodes.KEY_N: ('n', 'N'), ecodes.KEY_O: ('o', 'O'),
    ecodes.KEY_P: ('p', 'P'), ecodes.KEY_Q: ('q', 'Q'), ecodes.KEY_R: ('r', 'R'),
    ecodes.KEY_S: ('s', 'S'), ecodes.KEY_T: ('t', 'T'), ecodes.KEY_U: ('u', 'U'),
    ecodes.KEY_V: ('v', 'V'), ecodes.KEY_W: ('w', 'W'), ecodes.KEY_X: ('x', 'X'),
    ecodes.KEY_Y: ('y', 'Y'), ecodes.KEY_Z: ('z', 'Z'),
    ecodes.KEY_1: ('1', '!'), ecodes.KEY_2: ('2', '@'), ecodes.KEY_3: ('3', '#'),
    ecodes.KEY_4: ('4', '$'), ecodes.KEY_5: ('5', '%'), ecodes.KEY_6: ('6', '^'),
    ecodes.KEY_7: ('7', '&'), ecodes.KEY_8: ('8', '*'), ecodes.KEY_9: ('9', '('),
    ecodes.KEY_0: ('0', ')'),
    ecodes.KEY_MINUS: ('-', '_'), ecodes.KEY_EQUAL: ('=', '+'),
    ecodes.KEY_LEFTBRACE: ('[', '{'), ecodes.KEY_RIGHTBRACE: (']', '}'),
    ecodes.KEY_BACKSLASH: ('\\', '|'), ecodes.KEY_SEMICOLON: (';', ':'),
    ecodes.KEY_APOSTROPHE: ("'", '"'), ecodes.KEY_GRAVE: ('`', '~'),
    ecodes.KEY_COMMA: (',', '<'), ecodes.KEY_DOT: ('.', '>'), ecodes.KEY_SLASH: ('/', '?'),
    ecodes.KEY_SPACE: (' ', ' '),
}

SPECIAL_KEYS = {
    ecodes.KEY_TAB: get_sym("tab", "⇥", "Tab"),
    ecodes.KEY_ENTER: get_sym("enter", "⏎", "Enter"),
    ecodes.KEY_KPENTER: get_sym("enter", "⏎", "Enter"),
    ecodes.KEY_BACKSPACE: get_sym("backspace", "⌫", "Backspace"),
    ecodes.KEY_DELETE: get_sym("delete", "⌦", "Delete"),
    ecodes.KEY_ESC: get_sym("escape", "⎋", "Esc"),
    ecodes.KEY_CAPSLOCK: get_sym("caps_lock", "⇪", "CapsLock"),
    ecodes.KEY_UP: get_sym("up", "↑", "Up"),
    ecodes.KEY_DOWN: get_sym("down", "↓", "Down"),
    ecodes.KEY_LEFT: get_sym("left", "←", "Left"),
    ecodes.KEY_RIGHT: get_sym("right", "→", "Right"),
    ecodes.KEY_PAGEUP: get_sym("page_up", "PgUp", "PgUp"),
    ecodes.KEY_PAGEDOWN: get_sym("page_down", "PgDn", "PgDn"),
    ecodes.KEY_HOME: get_sym("home", "Home", "Home"),
    ecodes.KEY_END: get_sym("end", "End", "End"),
}

# Function keys F1-F12
for i in range(1, 13):
    fk_code = getattr(ecodes, f"KEY_F{i}", None)
    if fk_code:
        SPECIAL_KEYS[fk_code] = f"F{i}"

MOUSE_BUTTONS = {
    ecodes.BTN_LEFT: setting(CFG_MOUSE, "left_click", "LMB"),
    ecodes.BTN_RIGHT: setting(CFG_MOUSE, "right_click", "RMB"),
    ecodes.BTN_MIDDLE: setting(CFG_MOUSE, "middle_click", "MMB"),
    ecodes.BTN_SIDE: setting(CFG_MOUSE, "side_click", "Back"),
    ecodes.BTN_EXTRA: setting(CFG_MOUSE, "extra_click", "Fwd"),
}

MOD_SYMBOLS = dict(super=SYM_SUPER, ctrl=SYM_CTRL, alt=SYM_ALT, shift=SYM_SHIFT)

# Physical modifier identities avoid left/right and cross-keyboard release bugs.
MOD_ORDER = ('super', 'ctrl', 'alt', 'shift')
_held_mods: dict[tuple[str, int], str] = {}
_tap_mods: set[str] = set()
_mod_used = False
_caps_active = False
_key_buffer: deque[str] = deque(maxlen=BUFFER_SIZE)
_display_changed = asyncio.Event()
_last_input = 0.0


class NotificationDisplay:
    """One connection and one writer; fast input coalesces while a reply is pending."""

    def __init__(self) -> None:
        self.bus: MessageBus | None = None
        self.notification_id = 0

    async def call(self, member: str, signature: str = '', body: list | None = None) -> list:
        async with asyncio.timeout(3):
            reply = await self.bus.call(Message(
                destination='org.freedesktop.Notifications',
                path='/org/freedesktop/Notifications',
                interface='org.freedesktop.Notifications', member=member,
                signature=signature, body=body or [],
            ))
        if reply.message_type == MessageType.ERROR:
            raise RuntimeError(f'{reply.error_name}: {reply.body}')
        return reply.body

    async def connect(self) -> None:
        async with asyncio.timeout(3):
            self.bus = await MessageBus().connect()
        await self.call('GetCapabilities')

    async def show(self, items: tuple[str, ...]) -> None:
        text = SEPARATOR.join(items)
        # Summary is always plain text in the notification protocol. Rich text
        # belongs in the body; Mako's format must include %b to show it.
        body = html.escape(SEPARATOR).join(f'<b>{html.escape(item)}</b>' for item in items) if PANGO_MARKUP else ''
        hints = {
            'urgency': Variant('y', ('low', 'normal', 'critical').index(URGENCY)),
            'x-canonical-private-synchronous': Variant('s', SYNC_ID),
            'transient': Variant('b', True),
            'suppress-sound': Variant('b', True),
        }
        result = await self.call('Notify', 'susssasa{sv}i', [
            # Use the tag alone: Mako 1.11 can free the same notification twice
            # when an explicit replaces_id and its matching tag are both sent.
            APP_NAME, 0, '', text, body, [], hints,
            max(1, math.ceil(DISPLAY_TIMEOUT * 1000)),
        ])
        self.notification_id = result[0]

    async def close_notification(self) -> None:
        if self.notification_id:
            notification_id, self.notification_id = self.notification_id, 0
            # The server may have already expired it. Closing that ID is benign.
            try:
                await self.call('CloseNotification', 'u', [notification_id])
            except RuntimeError:
                pass

    async def run(self) -> None:
        while True:
            if not _key_buffer:
                await _display_changed.wait()
            else:
                remaining = _last_input + DISPLAY_TIMEOUT - asyncio.get_running_loop().time()
                if remaining > 0:
                    try:
                        await asyncio.wait_for(_display_changed.wait(), remaining)
                    except TimeoutError:
                        pass
                if not _display_changed.is_set():
                    _key_buffer.clear()
                    await self.close_notification()
                    continue
            _display_changed.clear()
            await self.show(tuple(_key_buffer))

    async def close(self) -> None:
        if self.bus is not None:
            try:
                if self.bus.connected:
                    await self.close_notification()
            except (OSError, EOFError, TimeoutError):
                pass
            finally:
                self.bus.disconnect()
                await self.bus.wait_for_disconnect()


def push_to_buffer(item: str) -> None:
    global _last_input
    _key_buffer.append(item)
    _last_input = asyncio.get_running_loop().time()
    _display_changed.set()


def process_event(event, device: str = '') -> None:
    global _mod_used, _caps_active
    if event.type == ecodes.EV_LED:
        if event.code == ecodes.LED_CAPSL:
            _caps_active = bool(event.value)
        return
    if event.type != ecodes.EV_KEY:
        return
    identity = (device, event.code)
    if event.code in MOD_MAP:
        mod_name, mod_sym = MOD_MAP[event.code]
        if event.value == 1:
            if not _held_mods:
                _tap_mods.clear()
                _mod_used = False
            _held_mods[identity] = mod_name
            _tap_mods.add(mod_name)
            if not SUPPRESS_PURE_MODS:
                push_to_buffer(mod_sym)
        elif event.value == 0 and identity in _held_mods:
            del _held_mods[identity]
            if not _held_mods:
                if SUPPRESS_PURE_MODS and not _mod_used:
                    symbols = [MOD_SYMBOLS[name] for name in MOD_ORDER if name in _tap_mods]
                    push_to_buffer(('' if COMPACT_SYMBOLS else '+').join(symbols))
                _tap_mods.clear()
                _mod_used = False
        return
    if event.value != 1:
        return
    if _held_mods:
        _mod_used = True
    if event.code == ecodes.KEY_CAPSLOCK:
        _caps_active = not _caps_active
    modifiers = set(_held_mods.values())
    shift = 'shift' in modifiers
    is_alpha = False
    special = False
    if event.code == ecodes.KEY_SPACE:
        char = SPACE_SYMBOL
        special = True
    elif event.code in KEYMAP:
        base, shifted = KEYMAP[event.code]
        is_alpha = base.isalpha()
        char = shifted if (shift ^ _caps_active if is_alpha else shift) else base
    elif event.code in SPECIAL_KEYS:
        char = SPECIAL_KEYS[event.code]
        special = True
    elif event.code in MOUSE_BUTTONS:
        if not ENABLE_MOUSE:
            return
        char = MOUSE_BUTTONS[event.code]
        special = True
    else:
        # Render media/keypad/additional function keys without mistaking buttons
        # on a touchpad/controller for keyboard keys.
        name = ecodes.KEY.get(event.code)
        if not isinstance(name, str):
            return
        char = name.removeprefix('KEY_')
        special = True
    if ENABLE_CHORDING and (modifiers & {'super', 'ctrl', 'alt'} or special and modifiers):
        symbols = [MOD_SYMBOLS[name] for name in MOD_ORDER if name in modifiers]
        char = char.upper() if is_alpha or len(char) == 1 else char
        char = (''.join(symbols) if COMPACT_SYMBOLS else '+'.join(symbols) + '+') + char
    push_to_buffer(char)


def release_device_modifiers(path: str) -> None:
    global _mod_used
    for identity in tuple(_held_mods):
        if identity[0] == path:
            del _held_mods[identity]
    if not _held_mods:
        _tap_mods.clear()
        _mod_used = False


async def read_device(dev: InputDevice) -> None:
    dropped = False
    try:
        while True:
            # Shield evdev's pending Future: cancellation otherwise races its
            # add_reader callback. close() below removes that callback.
            for event in await asyncio.shield(dev.async_read()):
                if event.type == ecodes.EV_SYN and event.code == ecodes.SYN_DROPPED:
                    # Ignore the incomplete stream until its next SYN_REPORT.
                    release_device_modifiers(dev.path)
                    dropped = True
                elif dropped:
                    if event.type == ecodes.EV_SYN and event.code == ecodes.SYN_REPORT:
                        seed_device_state(dev)
                        dropped = False
                else:
                    process_event(event, dev.path)
    except OSError:
        pass
    finally:
        release_device_modifiers(dev.path)
        dev.close()


def seed_device_state(dev: InputDevice) -> None:
    global _caps_active, _mod_used
    for code in dev.active_keys():
        if code in MOD_MAP:
            _held_mods[(dev.path, code)] = MOD_MAP[code][0]
            _mod_used = True  # Do not emit a tap for a press we never observed.
    if ecodes.LED_CAPSL in dev.capabilities().get(ecodes.EV_LED, []):
        _caps_active = ecodes.LED_CAPSL in dev.leds()


def scan_devices(tasks: dict[str, asyncio.Task[None]], skipped: dict[str, int]) -> None:
    for path, task in tuple(tasks.items()):
        if task.done():
            del tasks[path]
            task.result()
    paths = set(list_devices(writable=False))
    for path in tuple(skipped):
        if path not in paths:
            del skipped[path]
    for path in paths:
        if path in tasks:
            continue
        dev = None
        try:
            inode = os.stat(path).st_ino
            if skipped.get(path) == inode:
                continue
            skipped.pop(path, None)
            dev = InputDevice(path, readonly=True)
            keys = dev.capabilities().get(ecodes.EV_KEY, [])
            if ecodes.KEY_ENTER in keys or ENABLE_MOUSE and ecodes.BTN_LEFT in keys:
                seed_device_state(dev)
                tasks[path] = asyncio.create_task(read_device(dev))
                dev = None  # The reader owns it now.
            else:
                skipped[path] = inode
        except OSError:
            pass
        finally:
            if dev is not None:
                dev.close()


async def main() -> None:
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM, signal.SIGHUP):
        loop.add_signal_handler(sig, stop.set)
    display = NotificationDisplay()
    tasks: dict[str, asyncio.Task[None]] = {}
    skipped: dict[str, int] = {}
    writer = None
    try:
        await display.connect()
        scan_devices(tasks, skipped)
        if not tasks:
            raise RuntimeError('No readable, eligible keyboard/mouse devices')
        writer = asyncio.create_task(display.run())
        if READY_FILE:
            Path(READY_FILE).touch()
        print('Dusky Keys ready (physical key labels use the US key map).', flush=True)
        while not stop.is_set():
            if writer.done():
                writer.result()
            try:
                await asyncio.wait_for(stop.wait(), timeout=1)
            except TimeoutError:
                scan_devices(tasks, skipped)
    finally:
        for sig in (signal.SIGINT, signal.SIGTERM, signal.SIGHUP):
            loop.remove_signal_handler(sig)
        pending = tuple(tasks.values()) + ((writer,) if writer is not None else ())
        for task in pending:
            task.cancel()
        if pending:
            await asyncio.gather(*pending, return_exceptions=True)
        await display.close()


if __name__ == '__main__':
    try:
        asyncio.run(main())
    except (OSError, EOFError, RuntimeError, TimeoutError) as exc:
        sys.exit(f'[ENGINE ERROR] {exc}')
PYTHON_EOF
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
prepare_input_access
DK_READY_FILE="$READY_FILE" "$PYTHON_BIN" -B "$RUNNER_SCRIPT" {LOCK_FD}>&- &
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
rm -f -- "$READY_FILE"
release_lock
notify_user "Visualizer Enabled"
wait "$CHILD_PID" || exit "$?"
