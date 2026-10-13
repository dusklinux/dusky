#!/usr/bin/env bash
#d: Enable or disable TTY autologin

set -euo pipefail

readonly SYSTEMD_UNIT='getty@tty1.service'
readonly SYSTEMD_DIR="/etc/systemd/system/${SYSTEMD_UNIT}.d"
readonly OVERRIDE_FILE="${SYSTEMD_DIR}/override.conf"
readonly -a DISPLAY_MANAGERS=(greetd.service sddm.service display-manager.service
                             gdm.service lightdm.service lxdm.service ly.service)

MODE_AUTO=false
MODE_REVERT=false
MODE_STATUS=false
TARGET_USER=''
TARGET_HOME=''
declare -A UNIT_STATES=()
TEMP_FILE=''

log_info()    { printf '[INFO] %s\n' "$*"; }
log_success() { printf '[SUCCESS] %s\n' "$*"; }
log_error()   { printf '[ERROR] %s\n' "$*" >&2; }
die() { log_error "$*"; exit 1; }

cleanup() {
    if [[ -n $TEMP_FILE ]]; then
        rm -f -- "$TEMP_FILE"
    fi
}
trap cleanup EXIT
trap 'exit 130' INT
trap 'exit 143' TERM

parse_args() {
    while (( $# )); do
        case $1 in
            -a|--auto|--_confirmed) MODE_AUTO=true ;;
            -r|--revert) MODE_REVERT=true ;;
            -s|--status) MODE_STATUS=true ;;
            -u|--user)
                (( $# >= 2 )) && [[ -n $2 && $2 != -* ]] || die "$1 requires a username."
                TARGET_USER=$2
                shift
                ;;
            -h|--help)
                printf 'Usage: %s [OPTIONS]\n' "${0##*/}"
                printf '%s\n' \
                    '  -a, --auto        Skip interactive user selection' \
                    '  -r, --revert      Disable TTY autologin; prefer greetd, then SDDM, otherwise getty' \
                    '  -s, --status      Print true/false for configured TTY1 autologin (read-only)' \
                    '  -u, --user NAME   Select the account explicitly' \
                    '  -h, --help        Show this help'
                exit 0
                ;;
            *) die "Unknown argument: $1" ;;
        esac
        shift
    done
    [[ $MODE_STATUS != true || $MODE_REVERT != true ]] || die '--status and --revert are mutually exclusive.'
}

# Query the filesystem directly, both on a live system and in an offline chroot.
# Use the reported states, not is-enabled's aggregate exit status: static units
# return success despite not being enabled, while missing units return not-found.
load_unit_states() {
    local output state index
    local -a units=("${DISPLAY_MANAGERS[@]}" "$SYSTEMD_UNIT") states=()
    output=$(systemctl --root=/ is-enabled "${units[@]}") || :
    mapfile -t states <<< "$output"
    (( ${#states[@]} == ${#units[@]} )) || die 'Cannot query login service enablement.'
    UNIT_STATES=()
    for index in "${!units[@]}"; do
        state=${states[index]}
        [[ -n $state ]] || die "Cannot determine enablement of ${units[index]}."
        UNIT_STATES["${units[index]}"]=$state
    done
}

unit_is_enabled() {
    case ${UNIT_STATES[$1]:-not-found} in
        enabled|enabled-runtime|alias) return 0 ;;
        *) return 1 ;;
    esac
}

dm_is_enabled() {
    local dm
    for dm in "${DISPLAY_MANAGERS[@]}"; do
        if unit_is_enabled "$dm"; then
            return 0
        fi
    done
    return 1
}

# Only replace/remove the exact three-line drop-in owned by this script.
# Recognize the previous format as well, so existing installations can upgrade.
managed_override_exists() {
    [[ -f $OVERRIDE_FILE ]] || return 1
    local content name old new
    content=$(<"$OVERRIDE_FILE")
    local pattern='--autologin ([a-zA-Z0-9_][a-zA-Z0-9_.-]*) '
    [[ $content =~ $pattern ]] || return 1
    name=${BASH_REMATCH[1]}
    # shellcheck disable=SC2016 # systemd expands TERM, not this shell.
    old=$(printf '[Service]\nExecStart=\nExecStart=-/usr/bin/agetty --autologin %s --noclear --noissue %%I $TERM' "$name")
    new=$(override_content "$name")
    [[ $content == "$old" || $content == "$new" ]]
}

override_content() {
    # Match the current vendor getty's inherited TTY and terminal handling.
    # shellcheck disable=SC2016 # Keep systemd's environment substitution literal.
    printf '[Service]\nExecStart=\nExecStart=-/usr/bin/agetty --autologin %s --noreset --noclear --noissue -- - ${TERM}\n' "$1"
}

is_autologin_configured() {
    unit_is_enabled "$SYSTEMD_UNIT" && ! dm_is_enabled && managed_override_exists
}

# PID 1 can belong to the host while this process runs in an installer chroot.
# Compare both device and inode; inode alone can coincide across filesystems.
is_systemd_active() {
    local comm roots
    [[ -r /proc/1/comm ]] || return 1
    IFS= read -r comm < /proc/1/comm || return 1
    [[ $comm == systemd ]] || return 1
    roots=$(stat -Lc '%d:%i' /proc/1/root / 2>/dev/null) || return 1
    [[ ${roots%%$'\n'*} == "${roots#*$'\n'}" ]]
}

resolve_user() {
    local name uid _ record choice
    local -a users=()
    if [[ -z $TARGET_USER ]]; then
        if (( EUID != 0 )); then
            TARGET_USER=$(id -un)
        elif [[ -n ${SUDO_USER:-} && $SUDO_USER != root ]]; then
            TARGET_USER=$SUDO_USER
        else
            while IFS=: read -r name _ uid _ _ _ _; do
                if (( uid >= 1000 && uid < 65534 )); then
                    users+=("$name")
                fi
            done < /etc/passwd
            case ${#users[@]} in
                0) die 'No standard users found. Specify an account with --user NAME.' ;;
                1) TARGET_USER=${users[0]} ;;
                *)
                    [[ $MODE_AUTO != true ]] || die 'Multiple users found. Specify --user NAME with --auto.'
                    PS3='Select the account for TTY1 autologin: '
                    select choice in "${users[@]}"; do
                        if [[ -n $choice ]]; then
                            TARGET_USER=$choice
                            break
                        fi
                        log_error 'Choose a valid account number.'
                    done
                    [[ -n $TARGET_USER ]] || die 'No account selected.'
                    ;;
            esac
        fi
    fi
    record=$(getent passwd "$TARGET_USER") || die "Account not found: $TARGET_USER"
    IFS=: read -r TARGET_USER _ uid _ _ TARGET_HOME _ <<< "$record"
    [[ $TARGET_USER =~ ^[a-zA-Z0-9_][a-zA-Z0-9_.-]*$ ]] || die 'Account name cannot be represented in this autologin drop-in.'
    [[ $TARGET_HOME == /* && $TARGET_HOME != / ]] || die "Invalid home directory for $TARGET_USER."
}

sync_state_file() {
    local state=$1
    # Pass paths as arguments, never interpolate them into shell source. One
    # runuser invocation creates both paths and leaves them owned by the account.
    # shellcheck disable=SC2016 # The child shell expands its positional arguments.
    runuser --user "$TARGET_USER" -- bash -euc '
        state_dir=$1/.config/dusky/settings
        state_file=$state_dir/auto_login_tty
        if [[ -f $state_file && $(<"$state_file") == "$2" ]]; then
            exit 0
        fi
        mkdir -p -- "$state_dir"
        printf "%s\n" "$2" > "$state_file"
    ' bash "$TARGET_HOME" "$state"
}

reload_manager() {
    if is_systemd_active; then
        systemctl daemon-reload
    else
        log_info 'Offline environment: configuration will take effect on boot.'
    fi
}

do_setup() {
    [[ -x /usr/bin/agetty ]] || die '/usr/bin/agetty is missing.'
    case ${UNIT_STATES[$SYSTEMD_UNIT]:-not-found} in
        not-found|masked|masked-runtime) die "$SYSTEMD_UNIT is missing or masked." ;;
    esac
    if [[ -e $OVERRIDE_FILE || -L $OVERRIDE_FILE ]]; then
        managed_override_exists || die "$OVERRIDE_FILE contains unmanaged settings; leaving it intact."
    fi

    # Prepare a replacement only when the managed contents differ.
    local expected
    expected=$(override_content "$TARGET_USER")
    if [[ ! -f $OVERRIDE_FILE || $(<"$OVERRIDE_FILE") != "$expected" ]]; then
        mkdir -p -- "$SYSTEMD_DIR"
        TEMP_FILE=$(mktemp "${SYSTEMD_DIR}/.override.XXXXXXXX")
        printf '%s\n' "$expected" > "$TEMP_FILE"
        chmod 644 -- "$TEMP_FILE"
    fi
    # Ensure a boot login is available before disabling any greeter.
    if [[ ${UNIT_STATES[$SYSTEMD_UNIT]} != enabled ]]; then
        systemctl --root=/ --no-reload --quiet enable "$SYSTEMD_UNIT"
        load_unit_states
    fi
    unit_is_enabled "$SYSTEMD_UNIT" || die "$SYSTEMD_UNIT could not be enabled."

    local dm
    for dm in "${DISPLAY_MANAGERS[@]}"; do
        # Remove runtime links first: a persistent disable can also remove the
        # alias or link through which an externally installed unit was found.
        if unit_is_enabled "$dm"; then
            systemctl --root=/ --runtime --no-reload --quiet disable "$dm"
            load_unit_states
            if [[ ${UNIT_STATES[$dm]} != not-found ]]; then
                systemctl --root=/ --no-reload --quiet disable "$dm"
                load_unit_states
            fi
        fi
    done
    dm_is_enabled && die 'A display manager remains enabled; autologin was not configured.'
    # A greeter's Also= dependencies may have disabled getty as well.
    if ! unit_is_enabled "$SYSTEMD_UNIT"; then
        systemctl --root=/ --no-reload --quiet enable "$SYSTEMD_UNIT"
        load_unit_states
        if ! unit_is_enabled "$SYSTEMD_UNIT" || dm_is_enabled; then
            die 'Login service enablement conflicts with TTY1 autologin.'
        fi
    fi

    if [[ -n $TEMP_FILE ]]; then
        mv -f -- "$TEMP_FILE" "$OVERRIDE_FILE"
        TEMP_FILE=''
    fi
    # Reload on retries too: a previous invocation may have failed to reload
    # after publishing these same contents.
    reload_manager
    sync_state_file true
    log_success "TTY1 autologin configured for $TARGET_USER (takes effect next boot)."
}

do_revert() {
    if [[ -e $OVERRIDE_FILE || -L $OVERRIDE_FILE ]]; then
        managed_override_exists || die "$OVERRIDE_FILE contains unmanaged settings; leaving it intact."
    fi
    # Verify a boot login path before removing the managed override. Enabling
    # a unit can return success without installing links, so recheck its state.
    local dm
    if ! dm_is_enabled; then
        for dm in greetd.service sddm.service; do
            case ${UNIT_STATES[$dm]:-not-found} in
                disabled|linked|linked-runtime|indirect)
                    systemctl --root=/ --no-reload --quiet enable "$dm"
                    load_unit_states
                    if dm_is_enabled; then
                        break
                    fi
                    ;;
            esac
        done
    fi
    if ! dm_is_enabled; then
        case ${UNIT_STATES[$SYSTEMD_UNIT]:-not-found} in
            not-found|masked|masked-runtime)
                die "No display manager is enabled and $SYSTEMD_UNIT is missing or masked; revert incomplete."
                ;;
        esac
        if ! unit_is_enabled "$SYSTEMD_UNIT"; then
            systemctl --root=/ --no-reload --quiet enable "$SYSTEMD_UNIT"
            load_unit_states
        fi
        unit_is_enabled "$SYSTEMD_UNIT" || die "$SYSTEMD_UNIT could not be enabled; revert incomplete."
    fi
    if [[ -e $OVERRIDE_FILE || -L $OVERRIDE_FILE ]]; then
        rm -- "$OVERRIDE_FILE"
        # Other drop-ins are allowed to remain in this directory.
        if ! rmdir --ignore-fail-on-non-empty -- "$SYSTEMD_DIR"; then
            log_info "Retained drop-in directory: $SYSTEMD_DIR"
        fi
    fi
    reload_manager
    sync_state_file false
    if dm_is_enabled; then
        log_success 'TTY1 autologin override removed; display manager enabled for next boot with its existing login policy.'
    else
        log_success "TTY1 autologin override removed; $SYSTEMD_UNIT enabled for next boot."
    fi
}

main() {
    parse_args "$@"
    if [[ $MODE_STATUS == true ]]; then
        load_unit_states
        if is_autologin_configured; then
            printf 'true\n'
        else
            printf 'false\n'
        fi
        return 0
    fi
    resolve_user
    if (( EUID != 0 )); then
        local script
        local -a args=(--auto --user "$TARGET_USER")
        script=$(readlink -f -- "${BASH_SOURCE[0]}")
        [[ -f $script && -r $script ]] || die "Cannot re-execute script: $script"
        [[ $MODE_REVERT == true ]] && args+=(--revert)
        exec sudo -- bash "$script" "${args[@]}"
    fi
    # Serialize service changes and state writes across concurrent invocations.
    local lock_fd
    exec {lock_fd}> /run/dusky-tty-autologin.lock
    flock "$lock_fd"
    load_unit_states
    if [[ $MODE_REVERT == true ]]; then
        do_revert
    else
        do_setup
    fi
}

main "$@"
