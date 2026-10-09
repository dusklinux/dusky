#!/usr/bin/env bash
# -----------------------------------------------------------------------------
# Hyprland Animation Switcher for Rofi
# -----------------------------------------------------------------------------
set -euo pipefail

# -----------------------------------------------------------------------------
# CONFIGURATION
# -----------------------------------------------------------------------------
readonly CONFIG_DIR="${XDG_CONFIG_HOME:-$HOME/.config}"
readonly ANIM_DIR="$CONFIG_DIR/hypr/source/animations"
readonly LINK_DIR="$ANIM_DIR/active"
readonly DEST_FILE="$LINK_DIR/active.lua"
# Keep the historical spelling so existing installations retain their selection.
readonly STATE_FILE="$CONFIG_DIR/dusky/settings/dusky_animiation"
readonly LAST_ENABLED_FILE="${STATE_FILE}.last-enabled"
readonly FALLBACK_ANIM="dusky.lua"

# Visual Assets
readonly ICON_ACTIVE=""
readonly ICON_FILE=""
readonly ICON_DIR="󰹹"
readonly ICON_BACK=""
readonly ICON_ERROR=""
readonly ICON_DISABLE=""

# -----------------------------------------------------------------------------
# HELPER FUNCTIONS
# -----------------------------------------------------------------------------
notify_user() {
    local title="$1"
    local message="$2"
    local urgency="${3:-low}"
    # Only surface desktop notifications when invoked from rofi (rofi sets ROFI_INFO on selection).
    # Plain CLI/keybind invocations stay silent.
    [[ -n "${ROFI_INFO:-}" ]] || return 0
    if command -v notify-send &>/dev/null; then
        notify-send -u "$urgency" \
            --app-name=hypr-anim \
            --icon="${4:-applications-graphics}" \
            -h string:x-canonical-private-synchronous:hypr-anim \
            -t 1500 "$title" "$message" &>/dev/null || true
    fi
}

reload_hyprland() {
    hyprctl reload config-only >/dev/null || return 1
    local errors
    errors=$(hyprctl configerrors) || return 1
    if [[ -n "$errors" ]]; then
        printf 'Hyprland configuration errors:\n%s\n' "$errors" >&2
        return 1
    fi
}

escape_markup() {
    local s="$1"
    s="${s//&/"&amp;"}"
    s="${s//</"&lt;"}"
    s="${s//>/"&gt;"}"
    s="${s//\"/"&quot;"}"
    s="${s//\'/"&apos;"}"
    printf '%s' "$s"
}

# -----------------------------------------------------------------------------
# CORE LOGIC: STAGE, APPLY, VERIFY, COMMIT
# -----------------------------------------------------------------------------
apply_animation() (
    # The subshell keeps traps and temporary variables local to this transaction.
    local target_orient="$1" src_file="$2"
    case "$target_orient" in
        horizontal|vertical) [[ ! "$src_file" -ef "$ANIM_DIR/disable.lua" ]] || target_orient=disabled ;;
        disabled) src_file="$ANIM_DIR/disable.lua" ;;
        *) printf 'Invalid animation orientation: %s\n' "$target_orient" >&2; exit 1 ;;
    esac
    if [[ ! -f "$src_file" ]]; then
        printf 'Animation file missing: %s\n' "$src_file" >&2
        notify_user "Error" "Target file missing: $src_file" critical
        exit 1
    fi

    # Persist an absolute path so --toggle/--current work from any directory.
    src_file=$(realpath -e -- "$src_file")
    mkdir -p -- "$LINK_DIR" "${STATE_FILE%/*}"
    local tmp_dir installed=false committed=false index
    tmp_dir=$(mktemp -d "$LINK_DIR/.apply.XXXXXX")
    local -a destinations=("$DEST_FILE" "$STATE_FILE" "$LAST_ENABLED_FILE")
    # shellcheck disable=SC2329 # Invoked by the EXIT trap.
    cleanup_apply() {
        local result=$? restore_failed=false
        trap - EXIT
        if [[ "$installed" == true && "$committed" == false ]]; then
            for index in "${!destinations[@]}"; do
                if [[ -e "$tmp_dir/old.$index" || -L "$tmp_dir/old.$index" ]]; then
                    mv -fT -- "$tmp_dir/old.$index" "${destinations[index]}" || restore_failed=true
                else
                    rm -f -- "${destinations[index]}" || restore_failed=true
                fi
            done
            reload_hyprland || printf 'Failed to reload the restored configuration.\n' >&2
        fi
        if [[ "$restore_failed" == true ]]; then
            printf 'Rollback incomplete; recovery files retained in %s\n' "$tmp_dir" >&2
        else
            rm -rf -- "$tmp_dir"
        fi
        exit "$result"
    }
    trap cleanup_apply EXIT
    trap 'exit 130' INT
    trap 'exit 143' TERM

    for index in "${!destinations[@]}"; do
        if [[ -e "${destinations[index]}" || -L "${destinations[index]}" ]]; then
            cp -P -- "${destinations[index]}" "$tmp_dir/old.$index"
        fi
    done
    # Each preset has one orientation assignment; no comment-block parsing.
    local vertical=false
    [[ "$target_orient" != vertical ]] || vertical=true
    awk -v vertical="$vertical" '
        /^local vertical = (true|false) -- orientation$/ {
            $0 = "local vertical = " vertical " -- orientation"
        }
        { print }
    ' "$src_file" > "$tmp_dir/active.lua"
    chmod 644 "$tmp_dir/active.lua"
    printf '%s|%s\n' "$target_orient" "$src_file" > "$tmp_dir/state"
    if [[ "$target_orient" != disabled ]]; then
        printf '%s|%s\n' "$target_orient" "$src_file" > "$tmp_dir/last-enabled"
    elif [[ "$current_orient" != disabled && -f "$current_anim" && ! "$current_anim" -ef "$ANIM_DIR/disable.lua" ]]; then
        printf '%s|%s\n' "$current_orient" "$current_anim" > "$tmp_dir/last-enabled"
    fi

    # Same-directory rename atomically replaces regular files and symlinks.
    installed=true
    mv -fT -- "$tmp_dir/active.lua" "$DEST_FILE"
    if ! reload_hyprland; then
        notify_user "Animation Failed" "Hyprland rejected the configuration; restoring the previous preset." critical
        exit 1
    fi
    mv -fT -- "$tmp_dir/state" "$STATE_FILE"
    if [[ -f "$tmp_dir/last-enabled" ]]; then
        mv -fT -- "$tmp_dir/last-enabled" "$LAST_ENABLED_FILE"
    fi
    committed=true
    notify_user "Animation Applied" "${src_file##*/} (${target_orient^})"
)

# -----------------------------------------------------------------------------
# STRICT STATE RETRIEVAL
# -----------------------------------------------------------------------------
get_current_state() {
    current_orient="horizontal"
    current_anim=""

    if [[ -f "$STATE_FILE" ]]; then
        local saved_state
        saved_state=$(<"$STATE_FILE")

        if [[ "$saved_state" == *"|"* ]]; then
            current_orient="${saved_state%%|*}"
            current_anim="${saved_state#*|}"
        fi
    fi
    case "$current_orient" in
        horizontal|vertical|disabled) ;;
        *) current_orient=horizontal; current_anim="" ;;
    esac
    if [[ "$current_anim" -ef "$ANIM_DIR/disable.lua" ]]; then
        current_orient=disabled
    elif [[ "$current_orient" == disabled ]]; then
        current_anim="$ANIM_DIR/disable.lua"
    fi
}

# -----------------------------------------------------------------------------
# ROUTING & CLI FLAGS
# -----------------------------------------------------------------------------
# Serialize applies, including state reads, across tray and Rofi invocations.
exec {animation_lock}>"${XDG_RUNTIME_DIR:-/run/user/$UID}/dusky-animation.lock"
flock "$animation_lock"

if [[ "${1:-}" == "--toggle" ]]; then
    get_current_state
    live_state=$(hyprctl getoption animations:enabled -j) || exit 1
    if [[ ! "$live_state" =~ \"bool\":[[:space:]]*(true|false) ]]; then
        printf 'Could not read Hyprland animation state.\n' >&2
        exit 1
    fi
    if [[ "${BASH_REMATCH[1]}" == "true" ]]; then
        apply_animation disabled "$ANIM_DIR/disable.lua"
    else
        saved_state="$current_orient|$current_anim"
        [[ ! -f "$LAST_ENABLED_FILE" ]] || saved_state=$(<"$LAST_ENABLED_FILE")
        target_orient="${saved_state%%|*}"
        target_anim="${saved_state#*|}"
        if [[ "$target_orient" != "horizontal" && "$target_orient" != "vertical" ]] || [[ ! -f "$target_anim" || "$target_anim" -ef "$ANIM_DIR/disable.lua" ]]; then
            target_orient=horizontal
            target_anim="$ANIM_DIR/$FALLBACK_ANIM"
        fi
        apply_animation "$target_orient" "$target_anim"
    fi
    exit 0
fi

if [[ "${1:-}" == "--current" ]]; then
    get_current_state

    target_anim="$current_anim"
    if [[ -z "$target_anim" || ! -f "$target_anim" ]]; then
        target_anim="$ANIM_DIR/$FALLBACK_ANIM"
        [[ "$current_orient" != disabled ]] || current_orient=horizontal
    fi

    if [[ -f "$target_anim" ]]; then
        apply_animation "$current_orient" "$target_anim"
        exit 0
    else
        notify_user "Fatal Error" "System fallback animation missing." "critical"
        exit 1
    fi
fi

selection="${ROFI_INFO:-}"

if [[ -z "$selection" && $# -eq 2 && ("$1" == "horizontal" || "$1" == "vertical" || "$1" == "disabled") ]]; then
    selection="FILE:$1:$2"
fi

if [[ -z "$selection" && $# -gt 0 && -z "${ROFI_RETV:-}" ]]; then
    printf 'Usage: %s [--toggle | --current | horizontal|vertical|disabled FILE]\n' "${0##*/}" >&2
    exit 1
fi

# -----------------------------------------------------------------------------
# ROFI MENUS
# -----------------------------------------------------------------------------
get_current_state

# STEP 3: Apply Selection
if [[ "$selection" == FILE:* ]]; then
    payload="${selection#FILE:}"
    [[ "$payload" == *:* ]] || exit 1
    target_orient="${payload%%:*}"
    target_file="${payload#*:}"

    apply_animation "$target_orient" "$target_file"
    exit 0
fi

# STEP 2: Show Files
if [[ "$selection" == DIR:* ]]; then
    target_orient="${selection#DIR:}"
    [[ "$target_orient" == horizontal || "$target_orient" == vertical ]] || exit 1

    printf '\0prompt\x1fAnimations (%s)\n' "${target_orient^}"
    printf '\0markup-rows\x1ftrue\n'
    printf '\0no-custom\x1ftrue\n'
    printf '\0message\x1fSelect a configuration to apply instantly\n'

    printf '<span weight="bold">⬅ Back</span>\0icon\x1f%s\x1finfo\x1fBACK\n' "$ICON_BACK"

    shopt -s nullglob
    files=()
    for file in "$ANIM_DIR"/*.lua; do
        [[ -f "$file" && "${file##*/}" != disable.lua ]] && files+=("$file")
    done
    shopt -u nullglob

    if [[ ${#files[@]} -eq 0 ]]; then
        printf '%s\0icon\x1f%s\x1fnonselectable\x1ftrue\n' "No animation presets found" "$ICON_ERROR"
        exit 0
    fi

    for file in "${files[@]}"; do
        filename="${file##*/}"

        escaped_name=$(escape_markup "$filename")

        if [[ "$file" -ef "$current_anim" && "$target_orient" == "$current_orient" ]]; then
            printf "<span weight='bold'>%s</span> <span size='small' style='italic'>(Active)</span>\0icon\x1f%s\x1finfo\x1fFILE:%s:%s\n" \
                "$escaped_name" "$ICON_ACTIVE" "$target_orient" "$file"
        else
            printf '%s\0icon\x1f%s\x1finfo\x1fFILE:%s:%s\n' \
                "$escaped_name" "$ICON_FILE" "$target_orient" "$file"
        fi
    done
    exit 0
fi

# STEP 1: Main Menu
if [[ -z "$selection" || "$selection" == "BACK" ]]; then
    printf '\0prompt\x1fOrientation\n'
    printf '\0markup-rows\x1ftrue\n'
    printf '\0no-custom\x1ftrue\n'
    printf '\0message\x1fSelect animation layout orientation\n'

    # Check for disable.lua and list it as the first option if it exists
    if [[ -f "$ANIM_DIR/disable.lua" ]]; then
        if [[ "$current_anim" -ef "$ANIM_DIR/disable.lua" ]]; then
            printf '<span weight="bold">Disable Animations</span> <span size="small" style="italic">(Active)</span>\0icon\x1f%s\x1finfo\x1fFILE:disabled:%s/disable.lua\n' "$ICON_ACTIVE" "$ANIM_DIR"
        else
            printf 'Disable Animations\0icon\x1f%s\x1finfo\x1fFILE:disabled:%s/disable.lua\n' "$ICON_DISABLE" "$ANIM_DIR"
        fi
    fi

    if [[ "$current_orient" == "horizontal" && -f "$current_anim" && ! "$current_anim" -ef "$ANIM_DIR/disable.lua" ]]; then
        printf '<span weight="bold">Horizontal Animations</span> <span size="small" style="italic">(Active)</span>\0icon\x1f%s\x1finfo\x1fDIR:horizontal\n' "$ICON_ACTIVE"
    else
        printf 'Horizontal Animations\0icon\x1f%s\x1finfo\x1fDIR:horizontal\n' "$ICON_DIR"
    fi

    if [[ "$current_orient" == "vertical" && -f "$current_anim" && ! "$current_anim" -ef "$ANIM_DIR/disable.lua" ]]; then
        printf '<span weight="bold">Vertical Animations</span> <span size="small" style="italic">(Active)</span>\0icon\x1f%s\x1finfo\x1fDIR:vertical\n' "$ICON_ACTIVE"
    else
        printf 'Vertical Animations\0icon\x1f%s\x1finfo\x1fDIR:vertical\n' "$ICON_DIR"
    fi

    exit 0
fi
