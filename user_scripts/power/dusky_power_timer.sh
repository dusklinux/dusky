#!/usr/bin/env bash
# Requires Bash and systemd from the ISO baseline. No installation or daemon needed.
# Transient units belong to the current user and contain the resolved systemctl
# path, so this script need not stay running (or remain at the same path).
set -euo pipefail

readonly unit=dusky_power_timer

usage() {
    cat <<'EOF'
Usage: dusky_power_timer.sh [ACTION] DURATION
       dusky_power_timer.sh status | cancel | --help

Actions:
  shutdown / poweroff      Power off (default when ACTION is omitted)
  suspend                  Suspend to RAM
  sleep                    Use logind's configured SleepOperation policy
  hibernate                Hibernate using the system's existing configuration
  hybrid_sleep             Hibernate and suspend
  suspend_then_hibernate   Suspend, then hibernate using systemd's sleep policy

Examples:
  dusky_power_timer.sh 20s
  dusky_power_timer.sh shutdown 30min
  dusky_power_timer.sh suspend '1h 30min'
  dusky_power_timer.sh hibernate 2h

Durations use systemd time-span syntax; bare numbers mean seconds.
Only one timer per user is allowed. Cancel it before scheduling another.
The timer survives closing the terminal, but not reboot or user-manager exit
(normally after logout). Time spent suspended does not count toward the delay.
No wake alarm is installed. Existing logind inhibitors and hypridle sleep hooks
are respected. Hardware support, swap/resume setup and authorization must
already permit the selected action; this script does not configure them.
Cancellation cannot undo an action that logind has already accepted.

Inspect execution errors with:
  journalctl --user -u dusky_power_timer.service -b
EOF
}

die() { printf 'Power timer: %s\n' "$*" >&2; exit 1; }

case ${1-} in
    -h|--help)
        (( $# == 1 )) || die 'Use --help without other arguments.'
        usage
        exit
        ;;
    status)
        (( $# == 1 )) || die 'Use status without other arguments.'
        systemctl --user list-timers --all --full --no-pager "$unit.timer"
        exit
        ;;
    cancel)
        (( $# == 1 )) || die 'Use cancel without other arguments.'
        # A unit glob matches only loaded units, so cancellation is idempotent.
        # Stop the service too if the timer has already dispatched its command.
        systemctl --user stop "$unit.*"
        printf 'Power timer cancelled (if pending).\n'
        exit
        ;;
esac

case $# in
    1) action=poweroff; delay=$1 ;;
    2) action=$1; delay=$2 ;;
    *) usage >&2; exit 2 ;;
esac

case $action in
    shutdown|poweroff) action=poweroff; capability=CanPowerOff ;;
    suspend) capability=CanSuspend ;;
    sleep) capability=CanSleep ;;
    hibernate) capability=CanHibernate ;;
    hybrid_sleep) capability=CanHybridSleep ;;
    suspend_then_hibernate) capability=CanSuspendThenHibernate ;;
    *) die "Unknown action: $action (see --help)." ;;
esac

# Validate before asking systemd to create any units. Extract the native parser's
# microsecond result. LC_ALL=C makes the output label ASCII ("us:").
span=$(LC_ALL=C systemd-analyze timespan "$delay") || die 'Invalid duration.'
if [[ ! $span =~ us:[[:space:]]+([0-9]+)($|[[:space:]]) ]]; then
    die 'Cannot read the parsed duration.'
fi
delay_us=${BASH_REMATCH[1]}
# systemd reserves UINT64_MAX for infinity; it is not a usable timer delay.
[[ $delay_us != 18446744073709551615 ]] || die 'Duration must be finite.'
[[ $delay_us == *[1-9]* ]] || die 'Duration must be greater than zero.'

available=$(busctl --system call org.freedesktop.login1 /org/freedesktop/login1 \
    org.freedesktop.login1.Manager "$capability") || die 'Cannot query logind.'
case $available in
    's "yes"') ;;
    's "challenge"') die "$action requires authorization; permit it through system policy first." ;;
    *) die "$action is unavailable or inhibited (logind: $available)." ;;
esac

systemctl_path=$(command -v systemctl) || die 'systemctl is not installed.'
systemd-run --user --unit="$unit" --collect --service-type=exec \
    --expand-environment=no \
    --description="Dusky power timer: $action after $delay" \
    --on-active="${delay_us}us" \
    --timer-property=AccuracySec=1us \
    --timer-property=RemainAfterElapse=no \
    -- "$systemctl_path" --no-ask-password "${action//_/-}"
