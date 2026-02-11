#!/usr/bin/env bash
# ==============================================================================
# UNIVERSAL HYPRLAND MONITOR SCALER (V17 - HARDENED)
# ==============================================================================
# V17 CHANGELOG:
#   - CRITICAL: Replaced mv-based config writes with atomic cat > target
#     to preserve symlinks/inodes (from Dusky TUI Engine pattern).
#   - FIX: Global temp file lifecycle with guaranteed cleanup on all exits.
#   - FIX: Added -0 guard to float formatting in compute_next_scale.
#   - FIX: Trap hygiene — separate handlers for INT/TERM signals.
#   - FIX: Bash 5.0+ version guard.
#   - FIX: shopt -s extglob for defensive globbing.
#   - CLEAN: Removed redundant per-function trap that conflicted with global.
# ==============================================================================

set -euo pipefail
shopt -s extglob
export LC_ALL=C

# --- Immutable Configuration ---
readonly CONFIG_DIR="${HOME}/.config/hypr/edit_here/source"
readonly NOTIFY_TAG="hypr_scale_adjust"
readonly NOTIFY_TIMEOUT=2000
readonly MIN_LOGICAL_WIDTH=640
readonly MIN_LOGICAL_HEIGHT=360

# --- Runtime State ---
DEBUG="${DEBUG:-0}"
TARGET_MONITOR="${HYPR_SCALE_MONITOR:-}"
CONFIG_FILE=""

# Temp file global — single lifecycle, cleaned on any exit
declare _TMPFILE=""

# --- Logging ---
log_err()   { printf '\033[0;31m[ERROR]\033[0m %s\n' "$1" >&2; }
log_warn()  { printf '\033[0;33m[WARN]\033[0m %s\n'  "$1" >&2; }
log_info()  { printf '\033[0;32m[INFO]\033[0m %s\n'  "$1" >&2; }
log_debug() { [[ "${DEBUG}" != "1" ]] || printf '\033[0;34m[DEBUG]\033[0m %s\n' "$1" >&2; }

# --- Cleanup & Traps ---
cleanup() {
    if [[ -n "${_TMPFILE:-}" && -f "$_TMPFILE" ]]; then
        rm -f "$_TMPFILE" 2>/dev/null || :
    fi
}

trap cleanup EXIT
trap 'exit 130' INT
trap 'exit 143' TERM

die() {
    log_err "$1"
    notify-send -u critical "Monitor Scale Failed" "$1" 2>/dev/null || true
    exit 1
}

trim() {
    local s="$1"
    s="${s#"${s%%[![:space:]]*}"}"
    s="${s%"${s##*[![:space:]]}"}"
    printf '%s' "$s"
}

# --- Initialization ---
init_config_file() {
    if [[ -f "${CONFIG_DIR}/monitors.conf" ]]; then
        CONFIG_FILE="${CONFIG_DIR}/monitors.conf"
        log_debug "Selected config: monitors.conf"
    else
        CONFIG_FILE="${CONFIG_DIR}/monitors.conf"
        log_debug "Creating new config: monitors.conf"
        mkdir -p -- "${CONFIG_DIR}"
        : > "$CONFIG_FILE"
    fi
}

check_dependencies() {
    local missing=() cmd
    for cmd in hyprctl jq awk notify-send; do
        command -v "$cmd" &>/dev/null || missing+=("$cmd")
    done
    ((${#missing[@]} == 0)) || die "Missing dependencies: ${missing[*]}"
}

notify_user() {
    local scale="$1" monitor="$2" extra="${3:-}"
    log_info "Monitor: ${monitor} | Scale: ${scale}${extra:+ | ${extra}}"
    local body="Monitor: ${monitor}"
    [[ -n "$extra" ]] && body+=$'\n'"${extra}"
    notify-send -h "string:x-canonical-private-synchronous:${NOTIFY_TAG}" \
        -u low -t "$NOTIFY_TIMEOUT" "Display Scale: ${scale}" "$body" 2>/dev/null || true
}

# --- Scale Calculation (Strict Mode) ---
compute_next_scale() {
    local current="$1" direction="$2" phys_w="$3" phys_h="$4"

    awk -v cur="$current" -v dir="$direction" \
        -v w="$phys_w" -v h="$phys_h" \
        -v min_w="$MIN_LOGICAL_WIDTH" -v min_h="$MIN_LOGICAL_HEIGHT" '
    BEGIN {
        n = split("0.5 0.6 0.75 0.8 0.9 1.0 1.0625 1.1 1.125 1.15 1.2 1.25 1.33 1.4 1.5 1.6 1.67 1.75 1.8 1.88 2.0 2.25 2.4 2.5 2.67 2.8 3.0", raw)
        count = 0

        for (i = 1; i <= n; i++) {
            s = raw[i] + 0

            lw = w / s; lh = h / s
            if (lw < min_w || lh < min_h) continue

            frac = lw - int(lw)
            if (frac > 0.5) frac = 1.0 - frac
            if (frac > 0.01) continue

            valid[++count] = s
        }

        if (count == 0) { valid[1] = 1.0; count = 1 }

        best = 1; mindiff = 1e9
        for (i = 1; i <= count; i++) {
            d = cur - valid[i]
            if (d < 0) d = -d
            if (d < mindiff) { mindiff = d; best = i }
        }

        target = (dir == "+") ? best + 1 : best - 1
        if (target < 1) target = 1
        if (target > count) target = count

        ns = valid[target]
        changed = (((ns - cur)^2) > 0.000001) ? 1 : 0

        fmt = sprintf("%.6f", ns)
        sub(/0+$/, "", fmt); sub(/\.$/, "", fmt)
        # Guard against -0 (from template pattern)
        if (fmt == "-0") fmt = "0"
        printf "%s %d %d %d\n", fmt, int(w/ns + 0.5), int(h/ns + 0.5), changed
    }'
}

# --- Config Manager ---
update_config_file() {
    local monitor="$1" new_scale="$2"
    local found=0

    # Global temp file lifecycle — cleaned by EXIT trap
    _TMPFILE=$(mktemp "${CONFIG_FILE}.tmp.XXXXXXXXXX") || die "Failed to create temp file"

    log_debug "Updating config: ${monitor} -> ${new_scale}"

    while IFS= read -r line || [[ -n "$line" ]]; do
        if [[ "$line" =~ ^[[:space:]]*monitor[[:space:]]*= ]]; then
            local content="${line#*=}"
            content="${content%%#*}"
            content="$(trim "$content")"

            local -a fields
            IFS=',' read -ra fields <<< "$content"
            local mon_name
            mon_name="$(trim "${fields[0]}")"

            if [[ "$mon_name" == "$monitor" ]]; then
                found=1
                local new_line="monitor = ${mon_name}"
                new_line+=", $(trim "${fields[1]:-preferred}")"
                new_line+=", $(trim "${fields[2]:-auto}")"
                new_line+=", ${new_scale}"

                local i
                for ((i = 4; i < ${#fields[@]}; i++)); do
                    new_line+=", $(trim "${fields[i]}")"
                done

                printf '%s\n' "$new_line" >> "$_TMPFILE"
                continue
            fi
        fi
        printf '%s\n' "$line" >> "$_TMPFILE"
    done < "$CONFIG_FILE"

    if ((found == 0)); then
        log_info "Appending new entry for: ${monitor}"
        printf 'monitor = %s, preferred, auto, %s\n' "$monitor" "$new_scale" >> "$_TMPFILE"
    fi

    # CRITICAL: Use cat > target to preserve symlinks/inodes.
    # Do NOT use mv, as it breaks dotfile symlink chains.
    cat "$_TMPFILE" > "$CONFIG_FILE"
    rm -f "$_TMPFILE"
    _TMPFILE=""
}

# --- Main ---
format_refresh() { awk -v r="$1" 'BEGIN { fmt = sprintf("%.2f", r); sub(/\.00$/, "", fmt); print fmt }'; }

main() {
    # Bash version guard (from template)
    if (( BASH_VERSINFO[0] < 5 )); then
        log_err "Bash 5.0+ required"
        exit 1
    fi

    check_dependencies
    init_config_file

    if [[ $# -ne 1 ]] || [[ "$1" != "+" && "$1" != "-" ]]; then
        printf 'Usage: %s [+|-]\n' "${0##*/}" >&2; exit 1
    fi
    local direction="$1"

    local monitors_json
    monitors_json=$(hyprctl -j monitors) || die "Cannot connect to Hyprland"

    local monitor="${TARGET_MONITOR}"
    [[ -n "$monitor" ]] || monitor=$(jq -r '.[] | select(.focused) | .name // empty' <<< "$monitors_json")
    [[ -n "$monitor" ]] || monitor=$(jq -r '.[0].name // empty' <<< "$monitors_json")
    [[ -n "$monitor" ]] || die "No active monitors found"

    local props
    props=$(jq -r --arg m "$monitor" '.[] | select(.name == $m) | "\(.width) \(.height) \(.scale) \(.refreshRate) \(.x) \(.y)"' <<< "$monitors_json")
    [[ -n "$props" ]] || die "Monitor '${monitor}' details not found"

    local width height current_scale refresh pos_x pos_y
    read -r width height current_scale refresh pos_x pos_y <<< "$props"

    local scale_output new_scale logic_w logic_h changed
    scale_output=$(compute_next_scale "$current_scale" "$direction" "$width" "$height")
    read -r new_scale logic_w logic_h changed <<< "$scale_output"

    if ((changed == 0)); then
        log_warn "Limit reached: ${new_scale}"
        notify_user "$new_scale" "$monitor" "(Limit Reached)"
        exit 0
    fi

    update_config_file "$monitor" "$new_scale"

    local refresh_fmt rule
    refresh_fmt=$(format_refresh "$refresh")
    rule="${monitor},${width}x${height}@${refresh_fmt},${pos_x}x${pos_y},${new_scale}"

    log_info "Applying: ${rule}"

    if hyprctl keyword monitor "$rule" &>/dev/null; then
        sleep 0.15
        local actual_scale
        actual_scale=$(hyprctl -j monitors | jq -r --arg m "$monitor" '.[] | select(.name == $m) | .scale')

        if awk -v a="$actual_scale" -v b="$new_scale" 'BEGIN { exit !(((a - b)^2) > 0.000001) }'; then
            log_warn "Hyprland auto-adjusted: ${new_scale} -> ${actual_scale}"
            notify_user "Adjusted" "$monitor" "Requested ${new_scale}, got ${actual_scale}"
            update_config_file "$monitor" "$actual_scale"
        else
            notify_user "$new_scale" "$monitor" "Logical: ${logic_w}x${logic_h}"
        fi
    else
        die "Hyprland rejected rule: ${rule}"
    fi
}

main "$@"
