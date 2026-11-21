#!/usr/bin/env bash
# Native TLP profile selection and status for the Dusky desktop.
set -euo pipefail

readonly STATE_FILE="$HOME/.config/dusky/settings/tlp_state"
# Last profile the user explicitly chose (or "auto"); re-applied at login by `restore`.
readonly SELECTION_FILE="$HOME/.config/dusky/settings/tlp_selection"
readonly STATE_DIR="${STATE_FILE%/*}"
readonly LOCK_FILE="${XDG_RUNTIME_DIR:-$STATE_DIR}/tlp_toggle.lock"
readonly -a PROFILES=('power-saver' 'balanced' 'performance')
declare -rA ICON=(
    [performance]=$'\U000f04c5' [balanced]=$'\U000f007e'
    [power-saver]=$'\U000f032a' [unknown]='?'
)
declare -rA LABEL=(
    [performance]='Performance' [balanced]='Balanced'
    [power-saver]='Power Saver' [unknown]='Unknown'
)
declare -rA NOTIFY_ICON=(
    [performance]='battery-full-charged-symbolic'
    [balanced]='battery-good-symbolic'
    [power-saver]='battery-caution-symbolic'
)

err() { printf '[ERR] %s\n' "$*" >&2; exit 1; }

show_help() {
    cat <<'EOF'
tlp-toggle — TLP profile manager

USAGE
    tlp-toggle [toggle [--reverse]] Cycle profiles (power-saver → balanced → performance)
    tlp-toggle performance         Select Performance
    tlp-toggle balanced            Select Balanced
    tlp-toggle power-saver         Select Power Saver
    tlp-toggle auto                Apply saved settings and resume automatic operation
    tlp-toggle restore             Re-apply the last chosen profile (run at login)
    tlp-toggle status              Print active profile
    tlp-toggle status --json       Print Waybar JSON
    tlp-toggle status --probe      Print profile, power source, and mode
    tlp-toggle status --probe-json Print detailed JSON
    tlp-toggle -h | --help         Show help

Profile selection follows TLP_AUTO_SWITCH on subsequent power-source changes.
Explicit Performance selection attempts to clear userspace frequency caps.
Configured boost/performance settings and hardware limits still apply.
Switching requires root or existing non-interactive sudo authorization.
EOF
}

# Validate arguments before any filesystem writes or dependency checks.
action=${1:-toggle}
flag=${2:-}
(( $# <= 2 )) || err 'Too many arguments. Use --help.'
case "$action" in
    -h|--help|help)
        [[ -z $flag ]] || err 'Help takes no arguments.'
        show_help; exit 0 ;;
    toggle|-c|--cycle)
        case "$flag" in ''|--reverse|-r) ;; *) err "Unknown toggle option: $flag" ;; esac ;;
    status)
        case "$flag" in ''|--json|--probe|-p|--probe-json|--json-probe|-j) ;; *) err "Unknown status option: $flag" ;; esac ;;
    performance|balanced|power-saver|auto|restore)
        [[ -z $flag ]] || err "$action takes no arguments." ;;
    *) err "Unknown command: $action" ;;
esac
quiet=0
if [[ $action == restore ]]; then
    # Login hook: never fail loudly. No TLP, no saved choice, or "auto" means
    # TLP's own boot-time selection is already correct.
    command -v tlp-stat >/dev/null && command -v tlp >/dev/null || exit 0
    selection=''
    [[ -r $SELECTION_FILE ]] && read -r selection < "$SELECTION_FILE" || :
    case "$selection" in
        performance|balanced|power-saver) action=$selection; quiet=1 ;;
        *) exit 0 ;;
    esac
fi
command -v tlp-stat >/dev/null || err 'tlp-stat is not installed.'

current=unknown
mode=unknown
power_source=Unknown
parse_profile() {
    local raw=$1
    local profile=${raw%%/*}
    profile=${profile%% *}
    case "$profile" in
        performance|balanced|power-saver) current=$profile ;;
        *) current=unknown ;;
    esac
    if [[ $current == unknown ]]; then
        mode=unknown
    elif [[ $raw == *'(manual)'* ]]; then
        mode=manual
    else
        mode=auto
    fi
}

read_profile() {
    local output
    if ! output=$(LC_ALL=C tlp-stat -m); then
        err 'Unable to read the active TLP profile.'
    fi
    parse_profile "$output"
}

# Status reads do not lock, initialize, or trust the desktop cache.
if [[ $action == status ]]; then
    case "$flag" in
        --probe|-p|--probe-json|--json-probe|-j)
            if ! output=$(LC_ALL=C tlp-stat -s); then
                err 'Unable to read TLP status.'
            fi
            while IFS= read -r line; do
                if [[ $line =~ ^TLP[[:space:]]+profile[[:space:]]*=[[:space:]]*(.*)$ ]]; then
                    parse_profile "${BASH_REMATCH[1]}"
                elif [[ $line =~ ^Power[[:space:]]+source[[:space:]]*=[[:space:]]*(.*)$ ]]; then
                    case "${BASH_REMATCH[1]}" in
                        AC) power_source=AC ;;
                        battery|Battery) power_source=Battery ;;
                    esac
                fi
            done <<< "$output" ;;
        *) read_profile ;;
    esac
    case "$flag" in
        --json)
            printf '{"text":"%s %s","alt":"%s","class":"%s","tooltip":"Power profile: %s"}\n' \
                "${ICON[$current]}" "${LABEL[$current]}" "$current" "$current" "${LABEL[$current]}" ;;
        --probe|-p)
            printf 'Active Profile: %s\nPower Source:   %s\nMode:           %s\n' "$current" "$power_source" "$mode" ;;
        --probe-json|--json-probe|-j)
            printf '{"profile":"%s","power_source":"%s","mode":"%s"}\n' "$current" "$power_source" "$mode" ;;
        *) printf '%s\n' "$current" ;;
    esac
    exit 0
fi

command -v tlp >/dev/null || err 'TLP is not installed.'
mkdir -p -- "$STATE_DIR"
# Only mutations serialize. Queue quick successive clicks instead of discarding them.
exec {lock_fd}>"$LOCK_FILE"
flock -w 5 "$lock_fd" || err 'Another TLP switch is still running.'

case "$action" in
    toggle|-c|--cycle)
        read_profile
        index=-1
        for i in "${!PROFILES[@]}"; do
            if [[ ${PROFILES[i]} == "$current" ]]; then index=$i; break; fi
        done
        if (( index < 0 )); then
            target=balanced
        elif [[ $flag == --reverse || $flag == -r ]]; then
            target=${PROFILES[(index + 2) % 3]}
        else
            target=${PROFILES[(index + 1) % 3]}
        fi ;;
    auto) target=start ;;
    *) target=$action ;;
esac

# Always execute explicit selections: selecting the same profile also clears manual mode.
if (( EUID == 0 )); then
    command=(tlp "$target")
else
    command=(sudo -n tlp "$target")
fi
profile_command=("${command[@]}")
unclamp_requested=0
frequency_warning=''
if [[ $target == performance ]]; then
    # Linux frequency QoS uses S32_MAX as its unconstrained maximum request.
    # TLP writes the request with its existing privileges; each driver clamps
    # the effective limit to its own policy's capabilities. The request survives
    # Intel's turbo-off clamp until TLP enables turbo later in the same switch.
    # Deliberately override any configured PRF frequency ceiling for this click.
    for policy in /sys/devices/system/cpu/cpufreq/policy*; do
        [[ -f $policy/scaling_max_freq ]] || continue
        command+=(-- CPU_SCALING_MAX_FREQ_ON_PRF=2147483647)
        unclamp_requested=1
        break
    done
fi
if ! output=$("${command[@]}" 2>&1); then
    switch_failed=1
    if (( unclamp_requested )); then
        [[ -z $output ]] || printf '%s\n' "$output" >&2
        printf '[WARN] Frequency cap reset failed; retrying the normal Performance profile.\n' >&2
        frequency_warning='Frequency cap reset was skipped.'
        if output=$("${profile_command[@]}" 2>&1); then
            switch_failed=0
        fi
    fi
    if (( switch_failed )); then
        printf '%s\n' "$output" >&2
        if command -v notify-send >/dev/null; then
            (exec {lock_fd}>&-; notify-send --app-name=dusky-tlp --urgency=critical \
                --icon=dialog-error 'Power Profile Error' 'TLP failed. See command output for details.') >/dev/null 2>&1 &
        fi
        err "Failed to execute TLP command: $target"
    fi
fi
# Preserve TLP warnings rather than silently claiming every setting was applied.
[[ -z $output ]] || printf '%s\n' "$output" >&2
read_profile
if [[ $current == unknown || ( $target != start && $current != "$target" ) ]]; then
    err "TLP did not report the requested profile (reported: $current)."
fi

if [[ $target == performance ]]; then
    # TLP can return success despite a rejected sysfs write. Check effective
    # limits without failing a valid profile switch on unsupported/hotplug CPUs.
    for policy in /sys/devices/system/cpu/cpufreq/policy*; do
        [[ -r $policy/scaling_max_freq && -r $policy/cpuinfo_max_freq ]] || continue
        if ! { read -r actual < "$policy/scaling_max_freq" &&
               read -r maximum < "$policy/cpuinfo_max_freq"; } 2>/dev/null ||
           [[ ! $actual =~ ^[0-9]+$ || ! $maximum =~ ^[0-9]+$ ]]; then
            printf '[WARN] Unable to verify frequency limits for %s.\n' "${policy##*/}" >&2
            frequency_warning='Frequency limits could not be fully verified.'
        elif (( 10#$actual < 10#$maximum )); then
            printf '[WARN] %s remains limited to %s kHz (available maximum: %s kHz).\n' \
                "${policy##*/}" "$actual" "$maximum" >&2
            frequency_warning='A CPU frequency limit remains; see command output.'
        fi
    done
fi

# Keep the legacy cache for existing consumers, but never use it as runtime truth.
temporary=$(mktemp -- "${STATE_FILE}.XXXXXX")
trap 'rm -f -- "$temporary"' EXIT
printf '%s\n' "$current" > "$temporary"
mv -f -- "$temporary" "$STATE_FILE"
# Remember explicit choices so `restore` can re-apply them after a reboot.
if (( ! quiet )); then
    selection=$current
    [[ $target == start ]] && selection=auto
    printf '%s\n' "$selection" > "$temporary"
    mv -f -- "$temporary" "$SELECTION_FILE"
fi
if (( ! quiet )) && command -v notify-send >/dev/null; then
    (exec {lock_fd}>&-; notify-send --app-name=dusky-tlp --urgency=low \
        --icon="${NOTIFY_ICON[$current]}" \
        --hint=string:x-canonical-private-synchronous:power-profile \
        "TLP ${LABEL[$current]}" "${ICON[$current]}  ${LABEL[$current]}${frequency_warning:+ — $frequency_warning}") >/dev/null 2>&1 &
fi
