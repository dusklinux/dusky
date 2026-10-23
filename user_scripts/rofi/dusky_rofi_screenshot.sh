#!/usr/bin/env bash
# =====================================================================
# dusky_shot - Rofi/Wayland Screenshot Interactive Utility
# Architecture: Bash 5.3+ | Dusky Snap / Grim for all displays and active windows
# =====================================================================

set -euo pipefail

# --- Configuration & Paths ---
readonly APP_NAME="dusky_shot"
readonly NOTIFY_ICON="camera-photo-symbolic"
readonly NOTIFY_SYNC_ID="screenshot_timer_${BASHPID}"

BASE_PICS=$(xdg-user-dir PICTURES 2>/dev/null || true)
[[ -n "$BASE_PICS" ]] || BASE_PICS="$HOME/Pictures"
readonly BASE_PICS

readonly SAVE_DIR="${BASE_PICS}/Screenshots"

mkdir -p "$SAVE_DIR"

# --- Base Dependencies ---
declare -a REQ_CMDS=("rofi" "notify-send")
for cmd in "${REQ_CMDS[@]}"; do
    command -v "$cmd" >/dev/null || {
        printf '%s: missing dependency: %s\n' "$APP_NAME" "$cmd" >&2
        exit 1
    }
done

# --- Backend Mappings ---
declare -Ar TARGET_MAP=(
    ["Capture Everything"]="screen"
    ["Capture Active Display"]="output"
    ["Capture Selection"]="area"
    ["Capture Active Window"]="active"
)

declare -Ar ACTION_MAP=(
    ["Copy"]="copy"
    ["Save"]="save"
    ["Copy & Save"]="copysave"
    ["Annotate (Dusky Snap)"]="edit"
)

declare -ar TARGET_KEYS=(
    "Capture Everything"
    "Capture Active Display"
    "Capture Selection"
    "Capture Active Window"
)

declare -ar ACTION_KEYS=(
    "Copy"
    "Save"
    "Copy & Save"
    "Annotate (Dusky Snap)"
)

# --- Helper Functions ---

_make_target_path() {
    local ts
    printf -v ts '%(%Y%m%d_%H%M%S)T' -1
    printf '%s/%s_%s_%s.png\n' "$SAVE_DIR" "$APP_NAME" "$ts" "$BASHPID"
}

_notify() {
    local summary="$1"
    local body="${2:-}"
    local -a args=(-a "$APP_NAME" -i "$NOTIFY_ICON")

    [[ -n "${3:-}" ]] && args+=(-h "string:x-canonical-private-synchronous:$3")
    notify-send "${args[@]}" "$summary" "$body" || true
}

_require_cmds() {
    local cmd
    local -a missing=()

    for cmd in "$@"; do
        command -v "$cmd" >/dev/null || missing+=("$cmd")
    done

    ((${#missing[@]} == 0)) && return 0
    _notify "Execution Failed" "Missing dependency: ${missing[*]}"
    exit 1
}

_rofi_menu() {
    local prompt="$1"
    local output=""
    local status=0
    local config_path="${XDG_CONFIG_HOME:-$HOME/.config}/rofi/config.rasi"
    local -a rofi_args=(-dmenu -i -no-show-icons -no-custom -p "$prompt")
    shift

    [[ -f "$config_path" ]] && rofi_args+=(-config "$config_path")

    set +e
    output=$(
        printf '%s\n' "$@" | rofi "${rofi_args[@]}"
    )
    status=$?
    set -e

    case $status in
        0) printf '%s\n' "$output" ;;
        1) return 0 ;;
        *)
            _notify "Execution Failed" "Rofi encountered an error."
            exit 1
            ;;
    esac
}

# --- Cleanup Trap ---
TIMER_PID=""
cleanup() {
    [[ -n "${TIMER_PID:-}" ]] && kill "$TIMER_PID" 2>/dev/null || true
    notify-send -a "$APP_NAME" -C "$NOTIFY_SYNC_ID" 2>/dev/null || true
}
trap cleanup EXIT

_run_timer() {
    local seconds="$1"
    while (( seconds > 0 )); do
        _notify "Taking screenshot in ${seconds}s..." "" "$NOTIFY_SYNC_ID"
        sleep 1
        ((seconds--))
    done
    notify-send -a "$APP_NAME" -C "$NOTIFY_SYNC_ID" 2>/dev/null || true
}

# --- Capture: native Snap for region/display; Grim retains all-display/window capture. ---
_execute_capture() {
    local action="$1" target="$2" target_path geom
    target_path=$(_make_target_path)
    # Let Rofi's surface disappear before sampling the desktop.
    sleep 0.2

    if [[ "$target" == area || "$target" == output ]]; then
        _require_cmds dusky_snap
        local -a args=(--region)
        [[ "$target" == output ]] && args=(--fullscreen)
        case "$action" in
            copy) args+=(--copy) ;;
            save) args+=(--output "$target_path") ;;
            copysave) args+=(--copy --output "$target_path") ;;
            edit) ;; # Default capture opens the editor directly.
        esac
        dusky_snap "${args[@]}"
        return
    fi

    _require_cmds grim
    case "$action" in
        copy|copysave) _require_cmds wl-copy ;;
        edit) _require_cmds dusky_snap ;;
    esac
    case "$target" in
        screen)
            grim "$target_path"
            ;;
        active)
            _require_cmds hyprctl jq
            geom=$(hyprctl activewindow -j | jq -er '
                select(.mapped and .at and .size and .size[0] > 0 and .size[1] > 0)
                | "\(.at[0]),\(.at[1]) \(.size[0])x\(.size[1])"
            ') || {
                _notify "Capture Failed" "Unable to determine the active window."
                return 1
            }
            grim -g "$geom" "$target_path"
            ;;
    esac

    case "$action" in
        copy)
            wl-copy --type image/png < "$target_path"
            rm -f -- "$target_path"
            _notify "Screenshot Copied" "Saved directly to clipboard."
            ;;
        save)
            _notify "Screenshot Saved" "$target_path"
            ;;
        copysave)
            wl-copy --type image/png < "$target_path"
            _notify "Screenshot Copied & Saved" "$target_path"
            ;;
        edit)
            dusky_snap --file "$target_path"
            ;;
    esac
}

# --- Interactive Flow (Rofi) ---

# 1. Delay Mode
DELAY_OPT=$(_rofi_menu "Screenshot Mode" "Immediate" "Delayed")
[[ -z "$DELAY_OPT" ]] && exit 0

TIMER_SEC=0
case "$DELAY_OPT" in
    Immediate)
        ;;
    Delayed)
        TIMER_OPT=$(_rofi_menu "Timer Duration" "5s" "10s" "20s" "30s" "60s")
        [[ -z "$TIMER_OPT" ]] && exit 0

        case "$TIMER_OPT" in
            5s|10s|20s|30s|60s)
                TIMER_SEC="${TIMER_OPT%s}"
                ;;
            *)
                _notify "Execution Failed" "Invalid timer duration selected."
                exit 1
                ;;
        esac
        ;;
    *)
        _notify "Execution Failed" "Invalid screenshot mode selected."
        exit 1
        ;;
esac

# 2. Capture Target Selection
TARGET_OPT=$(_rofi_menu "Capture Target" "${TARGET_KEYS[@]}")
[[ -z "$TARGET_OPT" ]] && exit 0
FINAL_TARGET="${TARGET_MAP[$TARGET_OPT]-}"
[[ -n "$FINAL_TARGET" ]] || {
    _notify "Execution Failed" "Invalid capture target selected."
    exit 1
}

# 3. Action Selection
ACTION_OPT=$(_rofi_menu "Action" "${ACTION_KEYS[@]}")
[[ -z "$ACTION_OPT" ]] && exit 0
FINAL_ACTION="${ACTION_MAP[$ACTION_OPT]-}"
[[ -n "$FINAL_ACTION" ]] || {
    _notify "Execution Failed" "Invalid action selected."
    exit 1
}

# --- Execution ---

if (( TIMER_SEC > 0 )); then
    _run_timer "$TIMER_SEC" &
    TIMER_PID=$!
    wait "$TIMER_PID"
    TIMER_PID=""
fi

_execute_capture "$FINAL_ACTION" "$FINAL_TARGET"
