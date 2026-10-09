#!/usr/bin/env bash
# Set supported sysfs charge thresholds and optionally restore them at boot.
# Target: Linux 7.3+, Bash 5.3+, systemd 262+, Arch Linux.

set -euo pipefail

readonly SERVICE_NAME=battery-charge-limit.service
readonly SERVICE_FILE=/etc/systemd/system/${SERVICE_NAME}
readonly LOCK_FILE=/run/lock/battery-charge-limit.lock
readonly DEFAULT_LIMIT=80

if [[ -t 1 && -t 2 ]]; then
    readonly RED=$'\033[1;31m' GREEN=$'\033[1;32m' YELLOW=$'\033[1;33m'
    readonly BLUE=$'\033[1;34m' BOLD=$'\033[1m' NC=$'\033[0m'
else
    readonly RED='' GREEN='' YELLOW='' BLUE='' BOLD='' NC=''
fi

ACTION=interactive LIMIT='' PERSIST=''
TARGET_USER='' STATE_DIR='' STATE_FILE='' SERVICE_TMP=''
declare -a TARGET_FILES=()

info()    { printf '%s::%s %s%s%s\n' "$BLUE" "$NC" "$BOLD" "$*" "$NC"; }
success() { printf '%s==>%s %s%s%s\n' "$GREEN" "$NC" "$BOLD" "$*" "$NC"; }
warn()    { printf '%sWARNING:%s %s\n' "$YELLOW" "$NC" "$*" >&2; }
error()   { printf '%sERROR:%s %s\n' "$RED" "$NC" "$*" >&2; }
die()     { error "$*"; exit 1; }

require_root() {
    if (( EUID != 0 )); then
        local abs_path
        abs_path=$(realpath -e -- "$0") || die 'Cannot resolve script path.'
        info 'Escalating privileges via sudo...'
        exec sudo -- "$abs_path" "$@"
    fi
}

resolve_user_context() {
    # Direct root execution belongs to root; do not trust an inherited USER.
    TARGET_USER=${SUDO_USER:-$(id -un)}
    local entry user_home
    entry=$(getent passwd "$TARGET_USER") || die "No passwd entry for ${TARGET_USER}."
    IFS=: read -r TARGET_USER _ _ _ _ user_home _ <<< "$entry"
    [[ $user_home == /* ]] || die "Invalid home directory for ${TARGET_USER}."
    STATE_DIR=${user_home}/.config/dusky/settings
    STATE_FILE=${STATE_DIR}/battery_limiter
}

detect_batteries() {
    local supply type found_battery=0
    TARGET_FILES=()
    # Thresholds may belong to a charger, and battery names need not begin BAT.
    for supply in /sys/class/power_supply/*; do
        [[ -d $supply ]] || continue
        type=''
        { read -r type < "$supply/type"; } 2>/dev/null || true
        [[ $type != Battery ]] || found_battery=1
        if [[ -f $supply/charge_control_end_threshold ]]; then
            TARGET_FILES+=("$supply/charge_control_end_threshold")
        elif [[ -f $supply/stop_charge_thresh ]]; then
            TARGET_FILES+=("$supply/stop_charge_thresh")
        fi
    done
    if (( ${#TARGET_FILES[@]} == 0 )); then
        (( found_battery )) || die 'No batteries or charge-threshold devices detected.'
        die 'Your hardware/kernel exposes no supported sysfs charge threshold.'
    fi
}

read_threshold() {
    # cat distinguishes successful EOF from a kernel read error; read alone cannot.
    REPLY=$(cat -- "$1") || return 1
    REPLY=${REPLY//[[:space:]]/}
}

display_status() {
    local file name cached=''
    if [[ -f $STATE_FILE ]]; then
        { read -r cached < "$STATE_FILE"; } 2>/dev/null || true
        [[ $cached =~ ^([1-9][0-9]?|100)%$ ]] || cached=''
    fi
    info 'Current hardware thresholds:'
    for file in "${TARGET_FILES[@]}"; do
        name=${file%/*}
        name=${name##*/}
        if ! read_threshold "$file"; then
            printf '    %s -> [Unreadable; hardware state unknown]\n' "$name"
        elif [[ -n $REPLY ]]; then
            printf '    %s -> %s%%\n' "$name" "$REPLY"
        else
            printf '    %s -> [Empty readback; hardware state unverified]\n' "$name"
            [[ -z $cached ]] || printf '        Last successful request: %s (cached)\n' "$cached"
        fi
    done
    printf '\n'
}

update_user_state() {
    # Keep paths in argv instead of interpolating them into shell source.
    # Rename within the same directory so Waybar never reads a truncated value.
    # shellcheck disable=SC2016
    if runuser -u "$TARGET_USER" -- /usr/bin/bash -c '
        set -euo pipefail
        mkdir -p -- "$1"
        tmp=$(mktemp -- "$2.XXXXXX")
        trap '\''rm -f -- "$tmp"'\'' EXIT
        printf "%s%%\n" "$3" > "$tmp"
        chmod 644 -- "$tmp"
        mv -fT -- "$tmp" "$2"
    ' battery-limiter-state "$STATE_DIR" "$STATE_FILE" "$1"; then
        success "Dusky state updated: ${STATE_FILE} -> $1%"
    else
        error "Hardware was updated, but the Dusky state write failed: ${STATE_FILE}"
        return 1
    fi
}

apply_limits_live() {
    local limit=$1 file name failures=0
    info "Applying ${limit}% charge limit..."
    for file in "${TARGET_FILES[@]}"; do
        name=${file%/*}
        name=${name##*/}
        if read_threshold "$file" && [[ $REPLY == "$limit" ]]; then
            success "[${name}] Already set to ${limit}%"
            continue
        fi
        if ! printf '%s\n' "$limit" > "$file"; then
            error "[${name}] Kernel rejected ${limit}%; the requested value may be unsupported."
            failures=1
        elif ! read_threshold "$file"; then
            error "[${name}] Write accepted, but readback failed; hardware state is unknown."
            failures=1
        elif [[ $REPLY == "$limit" ]]; then
            success "[${name}] Verified at ${limit}%"
        elif [[ -z $REPLY ]]; then
            warn "[${name}] Write accepted with empty readback; ${limit}% is unverified."
        else
            error "[${name}] Requested ${limit}%, but hardware reports '${REPLY}'."
            failures=1
        fi
    done
    (( failures == 0 ))
}

write_service() {
    # Standalone boot command: no dependency on a user script or mounted home.
    # ':' disables systemd's dollar expansion; '%%' escapes its percent expansion.
    # Discover devices each boot, attempt all writes, and tolerate successful EOF.
    cat <<EOF
# Managed by battery_limiter.sh
[Unit]
Description=Hardware Battery Charge Limit ($1%%)
StartLimitIntervalSec=30
StartLimitBurst=5

[Service]
Type=oneshot
RemainAfterExit=yes
Restart=on-failure
RestartSec=2
TimeoutStartSec=30
EOF
    cat <<'EOF'
ExecStart=:/usr/bin/flock --no-fork /run/lock/battery-charge-limit.lock /usr/bin/bash -c '\
    found=0; failed=0; \
    for supply in /sys/class/power_supply/*; do \
        file="$supply/charge_control_end_threshold"; \
        [[ -f "$file" ]] || file="$supply/stop_charge_thresh"; \
        [[ -f "$file" ]] || continue; \
        found=1; \
        if ! printf "%%s\\n" "$1" > "$file"; then failed=1; continue; fi; \
        if ! value=$(cat -- "$file"); then failed=1; continue; fi; \
        value=${value//[[:space:]]/}; \
        if [[ -z "$value" ]]; then \
            printf "Write accepted; empty readback for %%s (unverified)\\n" "$file" >&2; \
        elif [[ "$value" != "$1" ]]; then \
            printf "Threshold mismatch for %%s: requested %%s, read %%s\\n" "$file" "$1" "$value" >&2; \
            failed=1; \
        fi; \
    done; \
    (( found && ! failed ))' battery-charge-limit \
EOF
    printf ' %s\n\n[Install]\nWantedBy=multi-user.target\n' "$1"
}

setup_persistence() {
    SERVICE_TMP=$(mktemp -- "/etc/systemd/system/.${SERVICE_NAME}.XXXXXX")
    write_service "$1" > "$SERVICE_TMP"
    chmod 644 -- "$SERVICE_TMP"
    mv -fT -- "$SERVICE_TMP" "$SERVICE_FILE"
    SERVICE_TMP=''
    # enable already performs daemon-reload; hardware was applied live above.
    systemctl enable "$SERVICE_NAME"
    success 'Charge limit will be restored at boot.'
}

remove_persistence() {
    if [[ -e $SERVICE_FILE || -L $SERVICE_FILE ]]; then
        # Stop retries before deleting the unit; propagate failures to the caller.
        systemctl disable --now "$SERVICE_NAME"
        rm -f -- "$SERVICE_FILE"
        systemctl daemon-reload
        success 'Systemd persistence removed; current hardware limits are unchanged.'
    else
        info 'No managed persistence configuration found.'
    fi
}

print_usage() {
    printf 'Usage: %s [OPTIONS]\n\n' "${0##*/}"
    cat <<'EOF'
  -l, --limit <1-100>   Set charge threshold (hardware may restrict values)
  -p, --persist         Restore limit at boot (requires --limit)
  -n, --no-persist      Remove persistence and apply live (requires --limit)
  -s, --status          Display current battery thresholds
  -r, --remove          Remove persistence without changing live thresholds
  -h, --help            Show help

Without -p, --limit removes existing persistence. No arguments: interactive mode.
EOF
}

validate_limit_input() { [[ $1 =~ ^([1-9][0-9]?|100)$ ]]; }

parse_args() {
    while (( $# )); do
        case $1 in
            -l|--limit)
                [[ $ACTION == interactive ]] || die 'Choose only one action: limit, status, or remove.'
                [[ -n ${2:-} ]] || die "Missing value for $1"
                validate_limit_input "$2" || die "Invalid limit: $2 (expected 1-100)."
                ACTION=apply LIMIT=$2
                shift 2 ;;
            -p|--persist|-n|--no-persist)
                [[ -z $PERSIST ]] || die 'Specify only one persistence flag.'
                if [[ $1 == -p || $1 == --persist ]]; then PERSIST=y; else PERSIST=n; fi
                shift ;;
            -s|--status|-r|--remove)
                [[ $ACTION == interactive ]] || die 'Choose only one action: limit, status, or remove.'
                if [[ $1 == -s || $1 == --status ]]; then ACTION=status; else ACTION=remove; fi
                shift ;;
            -h|--help) print_usage; exit 0 ;;
            *) die "Unknown parameter: $1" ;;
        esac
    done
    [[ -z $PERSIST || $ACTION == apply ]] || die 'Persistence flags require --limit.'
}

cleanup() {
    [[ -z $SERVICE_TMP ]] || rm -f -- "$SERVICE_TMP"
}

main() {
    parse_args "$@"
    if [[ $ACTION == status ]]; then
        resolve_user_context
        detect_batteries
        display_status
        return
    fi
    require_root "$@"
    trap cleanup EXIT
    if [[ $ACTION == remove ]]; then
        exec {lock_fd}> "$LOCK_FILE"
        flock "$lock_fd"
        remove_persistence
        return
    fi
    resolve_user_context
    detect_batteries
    if [[ $ACTION == interactive ]]; then
        display_status
        while true; do
            printf '%s?>%s Target charge limit (1-100) [Default: %s]: ' "$YELLOW" "$NC" "$DEFAULT_LIMIT"
            read -r LIMIT || die 'Input aborted.'
            LIMIT=${LIMIT:-$DEFAULT_LIMIT}
            validate_limit_input "$LIMIT" && break
            warn 'Invalid integer. Must be between 1 and 100.'
        done
        while true; do
            printf '%s?>%s Persist across reboots? (y/n) [Default: y]: ' "$YELLOW" "$NC"
            read -r PERSIST || die 'Input aborted.'
            PERSIST=${PERSIST:-y}
            case ${PERSIST,,} in
                y|yes) PERSIST=y; break ;;
                n|no) PERSIST=n; break ;;
                *) warn "Please answer 'y' or 'n'." ;;
            esac
        done
    fi
    # Serialize live writes, service installation/removal, and cache updates.
    exec {lock_fd}> "$LOCK_FILE"
    flock "$lock_fd"
    apply_limits_live "$LIMIT" || die 'Not all thresholds were applied; some devices may have changed.'
    if [[ $PERSIST == y ]]; then
        setup_persistence "$LIMIT"
    else
        remove_persistence
        info 'No boot restoration is configured; firmware may retain the live threshold.'
    fi
    update_user_state "$LIMIT" || return 1
    success 'Charge-limit request completed (see any readback warnings above).'
}

main "$@"
