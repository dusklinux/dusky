#!/usr/bin/env bash
# =====================================================================
# dusky_shot - Rofi/Wayland Screenshot Interactive Utility
# Architecture: Bash 5.3+ | Native Grim/Satty Pipeline
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
declare -a REQ_CMDS=("rofi" "grim" "notify-send")
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
    ["Annotate (Satty)"]="edit"
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
    "Annotate (Satty)"
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
    local config_path="$HOME/.config/rofi/config.rasi"
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

# --- Core Capture Logic (Native Implementation) ---
_execute_capture() {
    local action="$1"
    local target="$2"
    local geom=""
    local target_path=""
    local slurp_status=0
    local cmd_output=""
    local cmd_status=0
    local jq_status=0

    case "$target" in
        output|active) _require_cmds hyprctl jq ;;
        area) _require_cmds slurp ;;
    esac

    case "$action" in
        copy|copysave) _require_cmds wl-copy ;;
        edit) _require_cmds satty wl-copy ;;
    esac

    target_path=$(_make_target_path)

    # Allow Wayland compositors to clear the Rofi surface completely.
    sleep 0.2

    case "$target" in
        screen)
            ;;
        output)
            set +e
            cmd_output=$(hyprctl monitors -j)
            cmd_status=$?
            set -e
            (( cmd_status == 0 )) || {
                _notify "Capture Failed" "Unable to query monitors from Hyprland."
                exit 1
            }

            set +e
            geom=$(jq -r '.[] | select(.focused == true) | "\(.x),\(.y) \(.width)x\(.height)"' <<<"$cmd_output")
            jq_status=$?
            set -e
            (( jq_status == 0 )) || {
                _notify "Capture Failed" "Unable to parse focused display geometry."
                exit 1
            }

            [[ -n "$geom" ]] || {
                _notify "Capture Failed" "Unable to determine the focused display."
                exit 1
            }
            ;;
        active)
            set +e
            cmd_output=$(hyprctl activewindow -j)
            cmd_status=$?
            set -e
            (( cmd_status == 0 )) || {
                _notify "Capture Failed" "Unable to query the active window from Hyprland."
                exit 1
            }

            set +e
            geom=$(jq -r 'select(.at and .size) | "\(.at[0]),\(.at[1]) \(.size[0])x\(.size[1])"' <<<"$cmd_output")
            jq_status=$?
            set -e
            (( jq_status == 0 )) || {
                _notify "Capture Failed" "Unable to parse active window geometry."
                exit 1
            }

            [[ -n "$geom" ]] || {
                _notify "Capture Failed" "Unable to determine the active window."
                exit 1
            }
            ;;
        area)
            command -v hyprctl >/dev/null && \
                hyprctl keyword layerrule "match:namespace ^selection$, no_anim on" >/dev/null 2>&1 || true

            set +e
            geom=$(slurp)
            slurp_status=$?
            set -e

            [[ $slurp_status -ne 0 || -z "$geom" ]] && exit 0
            ;;
    esac

    if [[ -n "$geom" ]]; then
        grim -g "$geom" "$target_path" || {
            _notify "Capture Failed" "Grim encountered an error."
            exit 1
        }
    else
        grim "$target_path" || {
            _notify "Capture Failed" "Grim encountered an error."
            exit 1
        }
    fi

    case "$action" in
        copy)
            wl-copy --type image/png < "$target_path" || {
                _notify "Copy Failed" "wl-copy encountered an error."
                exit 1
            }
            rm -f -- "$target_path"
            _notify "Screenshot Copied" "Saved directly to clipboard."
            ;;
        save)
            _notify "Screenshot Saved" "$target_path"
            ;;
        copysave)
            wl-copy --type image/png < "$target_path" || {
                _notify "Copy Failed" "wl-copy encountered an error."
                exit 1
            }
            _notify "Screenshot Copied & Saved" "$target_path"
            ;;
        edit)
            satty --filename "$target_path" --output-filename "$target_path" \
                  --actions-on-enter "save-to-clipboard" \
                  --actions-on-escape "save-to-clipboard" \
                  --save-after-copy \
                  --copy-command "wl-copy" & disown
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
