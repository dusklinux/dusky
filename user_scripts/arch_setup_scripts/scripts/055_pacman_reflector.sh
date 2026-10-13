#!/usr/bin/env bash
#d: Fetch fresh HTTPS pacman mirrors without benchmarking

set -euo pipefail

readonly PACMAN_LOCK='/var/lib/pacman/db.lck'
readonly TARGET_FILE='/etc/pacman.d/mirrorlist'
readonly REFLECTOR_CONF='/etc/xdg/reflector/reflector.conf'
readonly -a REFLECTOR_ARGS=(--protocol https --latest 50 --connection-timeout 5)

# Standard Arch repositories use x86_64, including on optimized x86_64 systems.
# Keep pacman's $repo placeholders literal.
# shellcheck disable=SC2016
readonly FALLBACK_MIRRORS='## Global CDN fallback
Server = https://geo.mirror.pkgbuild.com/$repo/os/x86_64
Server = https://fastly.mirror.pkgbuild.com/$repo/os/x86_64'

CURRENT_TMP=''
cleanup() {
    if [[ -n $CURRENT_TMP ]]; then
        rm -f -- "$CURRENT_TMP" || true
    fi
}
trap cleanup EXIT
trap 'exit 130' INT
trap 'exit 143' TERM

if [[ -t 1 ]]; then
    readonly G=$'\e[32m' R=$'\e[31m' Y=$'\e[33m' B=$'\e[34m' NC=$'\e[0m'
else
    readonly G='' R='' Y='' B='' NC=''
fi

log_info() { printf '%s:: %s%s\n' "$B" "$1" "$NC"; }
log_ok()   { printf '%s:: %s%s\n' "$G" "$1" "$NC"; }
log_warn() { printf '%s:: %s%s\n' "$Y" "$1" "$NC"; }
log_err()  { printf '%s!! %s%s\n' "$R" "$1" "$NC" >&2; }

write_text_file_atomic() {
    local target=$1 content=$2 rc
    CURRENT_TMP=$(mktemp "${target}.tmp.XXXXXX") || return
    if printf '%s\n' "$content" > "$CURRENT_TMP" &&
       chmod 0644 "$CURRENT_TMP" &&
       mv -fT -- "$CURRENT_TMP" "$target"; then
        CURRENT_TMP=''
        return 0
    else
        rc=$?
    fi
    cleanup
    CURRENT_TMP=''
    return "$rc"
}

apply_arch_fallback_mirrorlist() {
    log_warn 'Applying global CDN fallback mirrors.'
    write_text_file_atomic "$TARGET_FILE" "$FALLBACK_MIRRORS"
}

has_active_mirrors() {
    grep -qE '^[[:space:]]*Server[[:space:]]*=[[:space:]]*https?://[^[:space:]]+' "$1" 2>/dev/null
}

# Never remove pacman's lock: descriptor inspection cannot prove it is stale.
# Use one shared wait budget, including races between our check and pacman.
run_pacman() {
    local deadline=$((BASH_MONOSECONDS + 60)) announced=0 rc
    CURRENT_TMP=$(mktemp) || return
    while :; do
        if [[ -e $PACMAN_LOCK ]]; then
            if (( BASH_MONOSECONDS >= deadline )); then
                log_err "Pacman lock remains after 60 seconds: $PACMAN_LOCK."
                cleanup
                CURRENT_TMP=''
                return 75
            fi
            if (( ! announced )); then
                log_warn 'Waiting for the pacman database lock to clear...'
                announced=1
            fi
            sleep 2 || return
            continue
        fi

        if LC_ALL=C pacman "$@" 2> "$CURRENT_TMP"; then
            # Preserve warnings from successful transactions too.
            cat -- "$CURRENT_TMP" >&2
            cleanup
            CURRENT_TMP=''
            return 0
        else
            rc=$?
        fi
        cat -- "$CURRENT_TMP" >&2
        if grep -qE 'unable to lock database|could not lock database' "$CURRENT_TMP"; then
            if (( BASH_MONOSECONDS < deadline )); then
                sleep 2 || return
                continue
            fi
            rc=75
        fi
        cleanup
        CURRENT_TMP=''
        return "$rc"
    done
}

is_cachyos() {
    local ID='' ID_LIKE=''
    [[ -r /etc/os-release ]] || return 1
    # os-release is a system-provided shell-compatible assignment file.
    # shellcheck source=/dev/null
    . /etc/os-release
    [[ $ID == cachyos || " $ID_LIKE " == *' cachyos '* ]]
}

configure_arch_timer() {
    local config
    # Reflector's argument-file parser accepts one argument per line.
    printf -v config '%s\n' --save "$TARGET_FILE" "${REFLECTOR_ARGS[@]}"
    if ! mkdir -p -- "${REFLECTOR_CONF%/*}" ||
       ! write_text_file_atomic "$REFLECTOR_CONF" "${config%$'\n'}"; then
        log_warn 'Could not configure automatic mirror updates.'
        return 0
    fi
    if systemctl enable --now reflector.timer; then
        log_ok 'Weekly reflector.timer enabled.'
    else
        log_warn 'Could not enable reflector.timer; mirrorlist was updated for this run.'
    fi
}

sync_arch() {
    local rc
    if ! has_active_mirrors "$TARGET_FILE"; then
        apply_arch_fallback_mirrorlist || return
    fi

    if ! command -v reflector &>/dev/null; then
        log_info 'Installing Reflector with a full system upgrade...'
        if run_pacman -Syu --needed --noconfirm reflector; then
            :
        else
            rc=$?
            (( rc != 75 && rc < 128 )) || return "$rc"
            apply_arch_fallback_mirrorlist || return
            if run_pacman -Syu --needed --noconfirm reflector; then
                :
            else
                rc=$?
                (( rc != 75 && rc < 128 )) || return "$rc"
                log_warn 'Reflector installation failed; continuing with CDN mirrors.'
            fi
        fi
    fi

    if command -v reflector &>/dev/null; then
        log_info 'Fetching 50 fresh HTTPS mirrors without benchmarking...'
        CURRENT_TMP=$(mktemp "${TARGET_FILE}.tmp.XXXXXX") || return
        if reflector "${REFLECTOR_ARGS[@]}" --save "$CURRENT_TMP" &&
           has_active_mirrors "$CURRENT_TMP"; then
            # Explicit checks are necessary even when callers test our status.
            chmod 0644 "$CURRENT_TMP" || return
            mv -fT -- "$CURRENT_TMP" "$TARGET_FILE" || return
            CURRENT_TMP=''
            log_ok 'Fresh HTTPS mirrorlist installed.'
            configure_arch_timer
        else
            cleanup
            CURRENT_TMP=''
            log_warn 'Reflector failed or returned no active mirrors; keeping the current list.'
        fi
    fi

    log_info 'Synchronizing pacman databases...'
    if run_pacman -Sy; then
        return 0
    else
        rc=$?
    fi
    (( rc != 75 && rc < 128 )) || return "$rc"
    apply_arch_fallback_mirrorlist || return
    # Force refresh only during recovery from a failed synchronization.
    if run_pacman -Syy; then
        return 0
    else
        rc=$?
        log_err 'Database synchronization failed even with fallback mirrors.'
        return "$rc"
    fi
}

main() {
    local rc=0
    if (( EUID != 0 )); then
        log_info 'Elevated privileges required.'
        exec sudo -- "$0" "$@"
    fi

    if is_cachyos; then
        log_info 'CachyOS detected; preserving its mirrors and skipping benchmarking.'
        run_pacman -Sy || rc=$?
    else
        sync_arch || rc=$?
    fi
    if (( rc != 0 )); then
        cleanup
        CURRENT_TMP=''
        # Mirror maintenance is best-effort; preserve deliberate cancellation.
        (( rc < 128 )) || return "$rc"
        log_warn "Mirror setup incomplete (status $rc); continuing without a successful database refresh."
        return 0
    fi
    log_ok 'Mirror setup complete.'
}

main "$@"
