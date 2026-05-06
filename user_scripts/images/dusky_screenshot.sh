#!/usr/bin/env bash
# ==============================================================================
# HYPRLAND SCREENSHOT ARCHITECTURE (THE UNBREAKABLE MASTER v10)
# Bash 5.3+ | Atomic IPC | Smart Click-Math | Perfect Freeze | Subshell Free
# ==============================================================================

set -euo pipefail

readonly SCRIPT_NAME="${0##*/}"
readonly PREFIX="screenshot"

readonly BASE_PICS=$(xdg-user-dir PICTURES 2>/dev/null || echo "$HOME/Pictures")
readonly SAVE_DIR="${BASE_PICS}/Screenshots"

# State variables
MODE="smart"
FULLSCREEN_MODE="focused" # Options: "focused" (current monitor) or "all" (every monitor)

declare -i COPY_CLIP=1
declare -i NOTIFY=1
declare -i ANNOTATE=0
declare -i FREEZE=0
declare -i HAS_ACTION_SUPPORT=0

SELECTION=""
TEMP_FILE=""
SATTY_TOOL=""
FREEZE_PID=""

# --- 1. ARGUMENT PARSING ---
while (($# > 0)); do
    case "$1" in
        -f|--fullscreen)   MODE="fullscreen"; shift ;;
        -r|--region)       MODE="region"; shift ;;
        -w|--window)       MODE="window"; shift ;;
        -s|--smart)        MODE="smart"; shift ;;
        -fz|--freeze)      FREEZE=1; shift ;;
        -a|--annotate)     ANNOTATE=1; shift ;;
        -t|--tool)         
            if (($# < 2)); then echo "Fatal: --tool requires a value." >&2; exit 1; fi
            SATTY_TOOL="$2"; ANNOTATE=1; shift 2 ;;
        --no-copy)         COPY_CLIP=0; shift ;;
        --no-notify)       NOTIFY=0; shift ;;
        -h|--help)
            cat <<EOF
Usage: $SCRIPT_NAME [OPTIONS]
  -s, --smart        Smart Mode: Drag to select, click to snap to window (Default)
  -f, --fullscreen   Capture screen (controlled by FULLSCREEN_MODE config)
  -r, --region       Draw a rectangle to capture
  -w, --window       Select a specific window
  -fz, --freeze      Freeze the screen while selecting
  -a, --annotate     Open Satty immediately after capturing
  -t, --tool <tool>  Open Satty with specific tool (arrow, blur, text, etc.)
  --no-copy          Do not copy to the clipboard
  --no-notify        Disable desktop notifications
EOF
            exit 0 ;;
        *) echo "Fatal: Unknown option: $1" >&2; exit 1 ;;
    esac
done

# --- 2. ENVIRONMENT & CAPABILITY POLLING ---
mkdir -p "$SAVE_DIR"

declare -a REQ_CMDS=("grim")
(( COPY_CLIP )) && REQ_CMDS+=("wl-copy")
(( NOTIFY ))    && REQ_CMDS+=("notify-send")
(( ANNOTATE ))  && REQ_CMDS+=("satty")
(( FREEZE ))    && REQ_CMDS+=("hyprpicker")

[[ "$MODE" == "region" ]] && REQ_CMDS+=("slurp")
[[ "$MODE" == "window" || "$MODE" == "smart" ]] && REQ_CMDS+=("slurp" "hyprctl" "jq")
[[ "$MODE" == "fullscreen" && "$FULLSCREEN_MODE" == "focused" ]] && REQ_CMDS+=("hyprctl" "jq")

for cmd in "${REQ_CMDS[@]}"; do
    command -v "$cmd" >/dev/null || { echo "Fatal: Missing dependency '$cmd'" >&2; exit 1; }
done

if (( NOTIFY )) && ! (( ANNOTATE )) && command -v satty >/dev/null; then
    while IFS= read -r capability; do
        if [[ "$capability" == "actions" ]]; then
            HAS_ACTION_SUPPORT=1
            break
        fi
    done < <(notify-send --capabilities 2>/dev/null || true)
fi

cleanup() {
    [[ -n "${TEMP_FILE:-}" && -f "$TEMP_FILE" ]] && rm -f "$TEMP_FILE"
    [[ -n "${FREEZE_PID:-}" ]] && kill "$FREEZE_PID" 2>/dev/null || true
}
trap cleanup EXIT

# --- 3. CONCURRENCY PREVENTER (Slurp Debounce) ---
if pkill -x slurp >/dev/null 2>&1; then
    exit 0 
fi

# --- 4. SCREEN FREEZING & WAYLAND IPC ---
freeze_screen() {
    if (( FREEZE )); then
        hyprpicker -r -z >/dev/null 2>&1 &
        FREEZE_PID=$!
        sleep 0.12 
    fi
}

unfreeze_screen() {
    if [[ -n "${FREEZE_PID:-}" ]]; then
        kill "$FREEZE_PID" 2>/dev/null || true
        FREEZE_PID=""
    fi
}

get_visible_clients() {
    local active_ws
    active_ws=$(hyprctl -j monitors | jq -c '[.[] | .activeWorkspace.name, .specialWorkspace.name | select(. != "")] | unique') || return 1
    
    hyprctl -j clients | jq -r --argjson ws "$active_ws" '
        [.[] | select(.mapped and (.hidden | not) and .size[0] > 0 and .size[1] > 0)
        | select(.workspace.name as $w | $ws | index($w))]
        | sort_by(.size[0] * .size[1])
        | .[] | "\(.at[0]),\(.at[1]) \(.size[0])x\(.size[1])"
    ' || return 1
}

# --- 5. SELECTION LOGIC ---

# Prevent black borders during slurp capture in Hyprland
if [[ "$MODE" =~ ^(region|window|smart)$ ]]; then
    command -v hyprctl >/dev/null && hyprctl keyword layerrule "match:namespace ^selection$, no_anim on" >/dev/null 2>&1 || true
fi

case "$MODE" in
    fullscreen)
        if [[ "$FULLSCREEN_MODE" == "focused" ]]; then
            SELECTION=$(hyprctl -j monitors | jq -r '
                def format_geo: .x as $x | .y as $y | (.width / .scale | floor) as $w | (.height / .scale | floor) as $h | .transform as $t | if ($t % 2) == 1 then "\($x),\($y) \($h)x\($w)" else "\($x),\($y) \($w)x\($h)" end;
                .[] | select(.focused == true) | format_geo
            ') || { echo "Fatal: Failed to query monitors." >&2; exit 1; }
            [[ -z "$SELECTION" ]] && exit 0
        fi
        ;;
    region)
        freeze_screen
        set +e
        SELECTION=$(slurp)
        STATUS=$?
        set -e
        
        [[ $STATUS -eq 1 || $STATUS -eq 143 ]] && exit 0 
        [[ $STATUS -ne 0 ]] && { echo "Fatal: Slurp failed." >&2; exit 1; }
        [[ -z "$SELECTION" ]] && exit 0
        ;;
    window)
        freeze_screen
        CLIENTS=$(get_visible_clients) || { echo "Fatal: Failed to query clients." >&2; exit 1; }
        [[ -z "$CLIENTS" ]] && exit 0 

        set +e
        SELECTION=$(slurp -r <<< "$CLIENTS")
        STATUS=$?
        set -e
        
        [[ $STATUS -eq 1 || $STATUS -eq 143 ]] && exit 0 
        [[ $STATUS -ne 0 ]] && { echo "Fatal: Slurp failed." >&2; exit 1; }
        [[ -z "$SELECTION" ]] && exit 0
        ;;
    smart)
        freeze_screen
        CLIENTS=$(get_visible_clients) || { echo "Fatal: Failed to query clients." >&2; exit 1; }
        
        MONITORS=$(hyprctl -j monitors | jq -r '
            def format_geo: .x as $x | .y as $y | (.width / .scale | floor) as $w | (.height / .scale | floor) as $h | .transform as $t | if ($t % 2) == 1 then "\($x),\($y) \($h)x\($w)" else "\($x),\($y) \($w)x\($h)" end;
            .[] | format_geo
        ') || { echo "Fatal: Failed to query monitors." >&2; exit 1; }
        
        RECTS=$(printf "%s\n%s" "$CLIENTS" "$MONITORS")

        set +e
        SELECTION=$(slurp <<< "$RECTS")
        STATUS=$?
        set -e

        [[ $STATUS -eq 1 || $STATUS -eq 143 ]] && exit 0 
        [[ $STATUS -ne 0 ]] && { echo "Fatal: Slurp failed." >&2; exit 1; }
        [[ -z "$SELECTION" ]] && exit 0

        if [[ $SELECTION =~ ^(-?[0-9]+),(-?[0-9]+)[[:space:]]([0-9]+)x([0-9]+)$ ]]; then
            w=$((10#${BASH_REMATCH[3]}))
            h=$((10#${BASH_REMATCH[4]}))
            
            if (( w * h < 20 )); then
                cx=${BASH_REMATCH[1]}
                cy=${BASH_REMATCH[2]}
                
                while IFS= read -r rect; do
                    [[ -z "$rect" ]] && continue
                    if [[ $rect =~ ^(-?[0-9]+),(-?[0-9]+)[[:space:]]([0-9]+)x([0-9]+) ]]; then
                        rx=${BASH_REMATCH[1]}
                        ry=${BASH_REMATCH[2]}
                        rw=$((10#${BASH_REMATCH[3]}))
                        rh=$((10#${BASH_REMATCH[4]}))
                        
                        if (( cx >= rx && cx < rx + rw && cy >= ry && cy < ry + rh )); then
                            SELECTION="${rx},${ry} ${rw}x${rh}"
                            break
                        fi
                    fi
                done <<< "$RECTS"
            fi
        fi
        ;;
esac

# --- 6. CAPTURE & UNFREEZE ---
TEMP_FILE=$(mktemp --tmpdir="$SAVE_DIR" ".${PREFIX}.XXXXXX.png")

if [[ "$MODE" == "fullscreen" && "$FULLSCREEN_MODE" == "all" ]]; then
    grim "$TEMP_FILE" || { echo "Fatal: Grim capture failed." >&2; exit 1; }
else
    grim -g "$SELECTION" "$TEMP_FILE" || { echo "Fatal: Grim capture failed." >&2; exit 1; }
fi

unfreeze_screen 

# --- 7. ATOMIC PUBLISHING (Timestamp & PID Based) ---
printf -v TS '%(%Y-%m-%d_%H-%M-%S)T' -1
FILENAME="${PREFIX}-${TS}_${BASHPID}.png"
FILE_PATH="${SAVE_DIR}/${FILENAME}"

if PUBLISH_ERR=$(mv -T --no-copy --update=none-fail -- "$TEMP_FILE" "$FILE_PATH" 2>&1); then
    TEMP_FILE="" 
else
    echo "Fatal: ${PUBLISH_ERR:-Publish failed. File collision detected.}" >&2
    exit 1
fi

# --- 8. ANNOTATION HANDLER ---
run_satty() {
    local -a satty_args=(
        "--filename" "$FILE_PATH" 
        "--output-filename" "$FILE_PATH" 
        "--early-exit" 
        "--disable-notifications"
    )
    [[ -n "$SATTY_TOOL" ]] && satty_args+=("--initial-tool" "$SATTY_TOOL")
    
    if (( COPY_CLIP )); then
        satty_args+=(
            "--actions-on-enter" "save-to-clipboard"
            "--actions-on-escape" "save-to-clipboard"
            "--save-after-copy"
            "--right-click-copy"
            "--copy-command" "wl-copy"
        )
    fi
    
    if ! satty "${satty_args[@]}"; then
        return 1
    fi
    return 0
}

# --- 9. DISPATCH & NOTIFICATIONS ---
if (( ANNOTATE )); then
    run_satty || true
elif (( COPY_CLIP )); then
    wl-copy --type image/png < "$FILE_PATH" || echo "Warning: Clipboard copy failed." >&2
fi

if (( NOTIFY )); then
    if (( ANNOTATE )) || ! (( HAS_ACTION_SUPPORT )); then
        notify-send -a "$SCRIPT_NAME" -i "$FILE_PATH" "Screenshot Captured" "Saved as $FILENAME" || true
    else
        (
            ACTION=$(notify-send -a "$SCRIPT_NAME" -i "$FILE_PATH" -t 8000 --action="edit=Annotate" "Screenshot Captured" "Saved as $FILENAME" 2>/dev/null || true)
            
            if [[ "$ACTION" == "edit" ]]; then
                if ! run_satty; then
                    notify-send -a "$SCRIPT_NAME" -i "$FILE_PATH" -u critical "Annotation Failed" "Satty encountered an error." || true
                fi
            fi
        ) >/dev/null 2>&1 & disown
    fi
fi
