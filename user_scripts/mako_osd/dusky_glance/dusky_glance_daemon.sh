#!/usr/bin/env bash
# Dusky Glance: single-process, single-notification system OSD for current Arch.
set -euo pipefail
shopt -s nullglob
umask 077

RUNTIME_DIR="${XDG_RUNTIME_DIR:-/run/user/$UID}"
GLANCE_STATE_DIR="$RUNTIME_DIR/dusky-glance"
MODE="${1:-}"

die() { printf 'dusky-glance: %s\n' "$*" >&2; exit 1; }
usage() {
    cat >&2 <<'USAGE'
Usage: dusky_glance_daemon.sh MODE [arguments]
  --alarm HH:MM [label]       Next local occurrence, one shot (toggle identical args)
  --timer [seconds]           Default 900
  --pomodoro [work] [rest]    Defaults 1500 300; rest may be zero
  --world-clock TZ [label]    Installed IANA timezone
  --disk-read|--disk-write|--disk-temp DEVICE
  --gpu-power|--gpu-usage|--gpu-mem cardN intel|amd|nvidia
  --hud [cardN intel|amd|nvidia]
  --stop | --stop-all         Stop every active Dusky Glance instance
  Other modes: --clock --clock-short --stopwatch --cpu-power --cpu --ram
  --ram-available --ram-free --ram-temp --zram --temp --battery --battery-percent --battery-watts
  --battery-time --disk --network* --uptime --workspace
USAGE
    exit 2
}
require_cmd() { command -v "$1" >/dev/null 2>&1 || die "Missing command: $1"; }

normalize_seconds() {
    local value="$1" minimum="$2"
    [[ "$value" =~ ^[0-9]+$ ]] || die 'Duration must contain decimal digits only'
    while [[ ${#value} -gt 1 && "$value" == 0* ]]; do value="${value#0}"; done
    (( ${#value} <= 10 )) || die 'Duration is too large'
    value=$((10#$value))
    (( value >= minimum && value <= 2147483647 )) || die 'Duration is out of range'
    printf '%s\n' "$value"
}

validate_label() {
    [[ -n "$1" && ${#1} -le 64 && "$1" != *$'\n'* && "$1" != *$'\r'* ]] ||
        die 'Label must be 1-64 characters on one line'
}
validate_gpu() {
    [[ "$1" =~ ^card[0-9]+$ ]] || die 'Expected a DRM card name such as card0'
    case "${2,,}" in intel|amd|nvidia) ;; *) die "Unsupported GPU vendor: $2" ;; esac
}

[[ -n "$MODE" ]] || usage
case "$MODE" in
    --stop|--stop-all)
        (( $# == 1 )) || usage ;;
    --clock|--clock-short|--stopwatch|--cpu-power|--cpu|--ram|\
    --ram-available|--ram-avail|--ram-free|\
    --ram-temp|--zram|--temp|--battery|--battery-percent|--battery-watts|\
    --battery-time|--disk|--network|--network-down|--network-download|\
    --network-up|--network-upload|--network-combined|--network-speed|\
    --network-rate|--network-down-session|--network-session-down|\
    --network-up-session|--network-session-up|--network-session|\
    --network-session-total|--network-total|--network-boot-down|\
    --network-boot-up|--network-boot|--network-down-boot|\
    --network-up-boot|--network-boot-total|--network-total-down|\
    --network-total-up|--uptime|--workspace)
        (( $# == 1 )) || usage ;;
    --timer)
        (( $# <= 2 )) || usage
        duration=$(normalize_seconds "${2:-900}" 1)
        set -- "$MODE" "$duration" ;;
    --pomodoro)
        (( $# <= 3 )) || usage
        work=$(normalize_seconds "${2:-1500}" 1)
        rest=$(normalize_seconds "${3:-300}" 0)
        set -- "$MODE" "$work" "$rest" ;;
    --alarm)
        (( $# >= 2 && $# <= 3 )) || usage
        [[ "$2" =~ ^([01][0-9]|2[0-3]):[0-5][0-9]$ ]] ||
            die 'Alarm time must be HH:MM (24-hour local time)'
        validate_label "${3:-Alarm}"
        set -- "$MODE" "$2" "${3:-Alarm}" ;;
    --world-clock)
        (( $# >= 2 && $# <= 3 )) || usage
        [[ "$2" != /* && "$2" != *..* && "$2" != :* &&
           -f "/usr/share/zoneinfo/$2" ]] || die 'Expected an installed IANA timezone'
        if ! { IFS= read -r -N 4 tz_magic < "/usr/share/zoneinfo/$2"; } 2>/dev/null ||
           [[ "$tz_magic" != TZif ]]; then
            die 'Expected an installed IANA timezone'
        fi
        validate_label "${3:-Time}"
        set -- "$MODE" "$2" "${3:-Time}" ;;
    --disk-read|--disk-write|--disk-temp)
        (( $# == 2 )) || usage
        [[ "$2" =~ ^[[:alnum:]_.+-]+$ && "$2" != . && "$2" != .. ]] ||
            die "Invalid block-device name: $2" ;;
    --gpu-power|--gpu-usage|--gpu-mem)
        (( $# == 3 )) || usage
        validate_gpu "$2" "$3"
        set -- "$MODE" "$2" "${3,,}" ;;
    --hud)
        (( $# == 1 || $# == 3 )) || usage
        if (( $# == 3 )); then
            validate_gpu "$2" "$3"
            set -- "$MODE" "$2" "${3,,}"
        fi ;;
    *) die "Unknown mode: $MODE" ;;
esac

for required in flock busctl sha256sum timeout stat mkfifo; do require_cmd "$required"; done

check_private_dir() {
    local perms
    [[ -d "$1" && -O "$1" && -w "$1" && ! -L "$1" ]] ||
        die "Directory must be private and owned by this user: $1"
    perms=$(stat -c %a -- "$1") || die "Cannot stat: $1"
    if [[ ! "$perms" =~ ^[0-7]{3,4}$ ]] || (( 8#$perms != 0700 )); then
        die "Expected mode 0700 on $1 (found $perms)"
    fi
}
check_private_dir "$RUNTIME_DIR"
mkdir -p -- "$GLANCE_STATE_DIR"
[[ -d "$GLANCE_STATE_DIR" && -O "$GLANCE_STATE_DIR" &&
   ! -L "$GLANCE_STATE_DIR" ]] || die 'Unsafe state directory'
state_mode=$(stat -c %a -- "$GLANCE_STATE_DIR") || die 'Cannot stat state directory'
if [[ "$state_mode" != 700 && "$state_mode" != 0700 ]]; then
    chmod 700 -- "$GLANCE_STATE_DIR"
fi
check_private_dir "$GLANCE_STATE_DIR"

# The first field after the final ') ' in /proc/PID/stat is field 3 (state).
# Consequently zero-based index 19 after stripping the comm is starttime (22).
process_start() {
    local pid="$1" raw
    local -a fields=()
    [[ "$pid" =~ ^[1-9][0-9]{0,9}$ ]] && (( pid > 1 )) || return 1
    { IFS= read -r raw < "/proc/$pid/stat"; } 2>/dev/null || return 1
    [[ "$raw" == "$pid ("* ]] || return 1
    read -r -a fields <<< "${raw##*) }"
    (( ${#fields[@]} >= 20 )) || return 1
    [[ "${fields[0]}" != Z && "${fields[0]}" != X && "${fields[0]}" != x &&
       "${fields[19]}" =~ ^[0-9]+$ ]] || return 1
    printf '%s\n' "${fields[19]}"
}
same_process() {
    local actual
    actual=$(process_start "$1") || return 1
    [[ "$actual" == "$2" ]]
}
request_stop() {
    local record="$1" pid="" started=""
    if [[ -L "$record" ]] || ! read -r pid started < "$record" 2>/dev/null ||
       ! same_process "$pid" "$started"; then
        rm -f -- "${record%.pid}.fifo" "$record"
        return 0
    fi
    # TERM interrupts the read wait and runs cleanup without a FIFO read.
    kill -TERM "$pid" 2>/dev/null || true
}
wait_records() {
    local record pid="" started="" attempt pending result=0
    local -a records=("$@")
    for ((attempt = 0; attempt < 120; attempt++)); do
        pending=0
        for record in "${records[@]}"; do
            if [[ -f "$record" ]] &&
               { read -r pid started < "$record"; } 2>/dev/null &&
               same_process "$pid" "$started"; then
                pending=1
                break
            fi
        done
        (( pending == 0 )) && break
        sleep 0.1
    done
    for record in "${records[@]}"; do
        if [[ -f "$record" ]] &&
           { read -r pid started < "$record"; } 2>/dev/null &&
           same_process "$pid" "$started"; then
            printf 'dusky-glance: PID %s did not stop: %s\n' "$pid" "$record" >&2
            result=1
        else
            rm -f -- "${record%.pid}.fifo" "$record"
        fi
    done
    return "$result"
}

[[ ! -L "$GLANCE_STATE_DIR/control.lock" ]] || die 'Unsafe control lock'
exec 9>"$GLANCE_STATE_DIR/control.lock"
flock -x 9
if [[ "$MODE" == --stop || "$MODE" == --stop-all ]]; then
    records=("$GLANCE_STATE_DIR"/*.pid)
    for record in "${records[@]}"; do request_stop "$record"; done
    wait_records "${records[@]}"
    exit $?
fi

case "$MODE" in
    --ram-avail) set -- --ram-available ;;
    --network-download) set -- --network-down ;;
    --network-upload) set -- --network-up ;;
    --network-speed|--network-rate) set -- --network-combined ;;
    --network-session-down) set -- --network-down-session ;;
    --network-session-up) set -- --network-up-session ;;
    --network-session-total|--network-total) set -- --network-session ;;
    --network-down-boot|--network-total-down) set -- --network-boot-down ;;
    --network-up-boot|--network-total-up) set -- --network-boot-up ;;
    --network-boot-total) set -- --network-boot ;;
esac
MODE="$1"
MODE_BASE="${MODE#--}"
instance_hash=$(printf '%s\0' "$@" | sha256sum)
instance_hash="${instance_hash%% *}"
PID_FILE="$GLANCE_STATE_DIR/${MODE_BASE}-${instance_hash}.pid"
FIFO_FILE="$GLANCE_STATE_DIR/${MODE_BASE}-${instance_hash}.fifo"
CURRENT_APP="dusky-glance-${MODE_BASE}"

if [[ -e "$PID_FILE" || -L "$PID_FILE" ]]; then
    old_pid="" old_start=""
    if [[ ! -L "$PID_FILE" ]] &&
       read -r old_pid old_start < "$PID_FILE" 2>/dev/null &&
       same_process "$old_pid" "$old_start"; then
        request_stop "$PID_FILE"
        wait_records "$PID_FILE"
        exit $?
    fi
    rm -f -- "$FIFO_FILE" "$PID_FILE"
fi
rm -f -- "$FIFO_FILE"

case "$MODE" in
    --alarm|--timer|--pomodoro) require_cmd notify-send ;;
    --disk) require_cmd df ;;
    --disk-read|--disk-write|--disk-temp)
        [[ -d "/sys/class/block/$2" ]] || die "Unknown block device: $2" ;;
    --gpu-power|--gpu-usage|--gpu-mem|--hud)
        if (( $# == 3 )); then
            [[ -d "/sys/class/drm/$2/device" ]] || die "Unknown DRM device: $2"
            read -r pci_vendor < "/sys/class/drm/$2/device/vendor" ||
                die "Cannot read PCI vendor for $2"
            case "$3" in intel) expected=0x8086 ;; amd) expected=0x1002 ;;
                nvidia) expected=0x10de ;; esac
            [[ "${pci_vendor,,}" == "$expected" ]] ||
                die "Vendor $3 does not match $2 ($pci_vendor)"
            if [[ "$3" == nvidia ]]; then require_cmd nvidia-smi; fi
        fi ;;
    --workspace) require_cmd hyprctl ;;
esac

MY_PID=$BASHPID
MY_START=$(process_start "$MY_PID") || die 'Cannot read own process start time'
OSD_ID=0
OSD_OWNER=""
NEXT_NOTIFY_TRY=0
LAST_NOTIFY_WARNING=-30
LAST_OSD=-8
LAST_BODY=""
NEXT_TICK=0
TICK_PERIOD_US=1000000
HAS_RECORD=0
FIFO_OPEN=0

warn_notification() {
    if (( SECONDS - LAST_NOTIFY_WARNING >= 30 )); then
        printf 'dusky-glance: notification delivery failed; retrying\n' >&2
        LAST_NOTIFY_WARNING=$SECONDS
    fi
    NEXT_NOTIFY_TRY=$((SECONDS + 5))
}
notification_owner() {
    local reply kind owner
    if ! reply=$(busctl --user --timeout=2 -- call \
        org.freedesktop.DBus /org/freedesktop/DBus \
        org.freedesktop.DBus GetNameOwner \
        s org.freedesktop.Notifications 2>/dev/null); then
        busctl --user --timeout=3 -- call \
            org.freedesktop.DBus /org/freedesktop/DBus \
            org.freedesktop.DBus StartServiceByName \
            su org.freedesktop.Notifications 0 >/dev/null 2>&1 || return 1
        reply=$(busctl --user --timeout=2 -- call \
            org.freedesktop.DBus /org/freedesktop/DBus \
            org.freedesktop.DBus GetNameOwner \
            s org.freedesktop.Notifications 2>/dev/null) || return 1
    fi
    read -r kind owner <<< "$reply"
    [[ "$kind" == s ]] || return 1
    owner="${owner#\"}"
    owner="${owner%\"}"
    [[ "$owner" == :* ]] || return 1
    printf '%s\n' "$owner"
}
clear_osd() {
    if [[ -n "$OSD_OWNER" ]] && (( OSD_ID > 0 )); then
        busctl --user --timeout=2 -- call \
            "$OSD_OWNER" /org/freedesktop/Notifications \
            org.freedesktop.Notifications CloseNotification \
            u "$OSD_ID" >/dev/null 2>&1 || true
    fi
    OSD_ID=0
}
pause_for() {
    local message=""
    if IFS= read -r -t "$1" -u 7 message 2>/dev/null; then
        [[ "$message" == stop ]] && exit 0
    fi
    return 0
}
wait_tick() {
    local now="" wait_us=1000000 delay=1
    if boottime_us now; then
        if (( NEXT_TICK == 0 )); then
            NEXT_TICK=$((now + TICK_PERIOD_US))
        elif (( NEXT_TICK <= now )); then
            NEXT_TICK=$((NEXT_TICK + ((now - NEXT_TICK) / TICK_PERIOD_US + 1) * TICK_PERIOD_US))
        fi
        wait_us=$((NEXT_TICK - now))
        NEXT_TICK=$((NEXT_TICK + TICK_PERIOD_US))
        printf -v delay '%d.%06d' "$((wait_us / 1000000))" "$((wait_us % 1000000))"
    else
        NEXT_TICK=0
    fi
    pause_for "$delay"
}
cleanup() {
    local pid="" started=""
    trap '' INT TERM
    clear_osd
    if (( HAS_RECORD )); then
        if [[ -f "$PID_FILE" && ! -L "$PID_FILE" ]] &&
           read -r pid started < "$PID_FILE" 2>/dev/null &&
           [[ "$pid" == "$MY_PID" && "$started" == "$MY_START" ]]; then
            rm -f -- "$FIFO_FILE" || true
            rm -f -- "$PID_FILE" || true
        fi
    else
        rm -f -- "$FIFO_FILE" || true
    fi
    if (( FIFO_OPEN )); then exec 7>&- || true; fi
}
trap cleanup EXIT
trap 'exit 0' INT TERM
mkfifo -m 600 -- "$FIFO_FILE" || die 'Cannot create control FIFO'
exec 7<> "$FIFO_FILE" || die 'Cannot open control FIFO'
FIFO_OPEN=1
if ! printf '%s %s\n' "$MY_PID" "$MY_START" > "$PID_FILE"; then
    rm -f -- "$PID_FILE"
    die 'Cannot write PID record'
fi
HAS_RECORD=1
flock -u 9
exec 9>&-

escape_markup() {
    local -n _dest=$1
    local _escaped=$2
    _escaped="${_escaped//&/'&amp;'}"
    _escaped="${_escaped//</'&lt;'}"
    _escaped="${_escaped//>/'&gt;'}"
    _dest=$_escaped
}
send_body() {
    local reply kind new_id
    if (( OSD_ID > 0 && SECONDS - LAST_OSD < 8 )) && [[ "$1" == "$LAST_BODY" ]]; then
        return 0
    fi
    (( SECONDS >= NEXT_NOTIFY_TRY )) || return 0
    if [[ -z "$OSD_OWNER" ]]; then
        if ! OSD_OWNER=$(notification_owner); then
            OSD_OWNER=""
            warn_notification
            return 0
        fi
        OSD_ID=0
    fi
    if ! reply=$(busctl --user --timeout=3 -- call \
        "$OSD_OWNER" /org/freedesktop/Notifications \
        org.freedesktop.Notifications Notify \
        'susssasa{sv}i' "$CURRENT_APP" "$OSD_ID" '' ' ' "$1" \
        0 0 15000 2>/dev/null); then
        OSD_OWNER="" OSD_ID=0 LAST_BODY=""
        warn_notification
        return 0
    fi
    read -r kind new_id <<< "$reply"
    if [[ "$kind" == u && "$new_id" =~ ^[1-9][0-9]{0,9}$ ]] &&
       (( new_id <= 4294967295 )); then
        OSD_ID=$new_id
        LAST_BODY="$1"
        LAST_OSD=$SECONDS
    else
        OSD_OWNER="" OSD_ID=0 LAST_BODY=""
        warn_notification
    fi
    return 0
}
send_osd() {
    local safe
    escape_markup safe "$1"
    send_body "<span font='${2:-monospace 20}' weight='bold'>${safe}</span>"
}
send_hud_osd() { send_osd "$1" 'monospace 9'; }
send_world_clock_osd() {
    local time_str place diff
    escape_markup time_str "$1"
    escape_markup place "$2"
    escape_markup diff "$3"
    send_body "<span font='monospace 11' weight='bold'>${time_str}</span>"$'\n'"<span font='monospace 9'>${place} • ${diff}</span>"
}

# /proc/uptime's first field uses CLOCK_BOOTTIME: elapsed time includes suspend.
boottime_us() {
    local -n _dest=$1
    local _line _whole _fraction
    { read -r _line _ < /proc/uptime; } 2>/dev/null || return 1
    [[ "$_line" =~ ^[0-9]+\.[0-9]+$ ]] || return 1
    _whole="${_line%%.*}"
    _fraction="${_line#*.}000000"
    _fraction="${_fraction:0:6}"
    (( ${#_whole} <= 12 )) || return 1
    _dest=$((10#$_whole * 1000000 + 10#$_fraction))
}
format_time() {
    local -n _dest=$1
    local _h=$(( $2 / 3600 )) _m=$(( ($2 % 3600) / 60 )) _s=$(( $2 % 60 ))
    if (( _h > 0 )); then
        printf -v _dest '%02d:%02d:%02d' "$_h" "$_m" "$_s"
    else
        printf -v _dest '%02d:%02d' "$_m" "$_s"
    fi
}
read_uint() {
    local -n _dest=$1
    local _digits="" _limit="${3:-18}"
    { IFS= read -r _digits < "$2"; } 2>/dev/null || return 1
    [[ "$_digits" =~ ^[0-9]+$ ]] || return 1
    while [[ ${#_digits} -gt 1 && "$_digits" == 0* ]]; do _digits="${_digits#0}"; done
    (( ${#_digits} <= _limit )) || return 1
    _dest=$((10#$_digits))
}
read_abs() {
    local -n _dest=$1
    local _digits=""
    { IFS= read -r _digits < "$2"; } 2>/dev/null || return 1
    [[ "$_digits" =~ ^-?[0-9]{1,12}$ ]] || return 1
    _digits="${_digits#-}"
    _dest=$((10#$_digits))
}
read_temp_c() {
    local -n _dest=$1
    local _raw="" _degrees
    { IFS= read -r _raw < "$2"; } 2>/dev/null || return 1
    [[ "$_raw" =~ ^-?[0-9]{1,8}$ ]] || return 1
    _degrees=$((10#${_raw#-} / 1000))
    if [[ "$_raw" == -* ]]; then _degrees=$((-_degrees)); fi
    _dest="${_degrees}°C"
    return 0
}
play_sound() {
    if [[ ! -f "$1" ]]; then
        printf 'dusky-glance: Alarm sound unavailable: %s\n' "$1" >&2
        return 0
    fi
    if command -v pw-play >/dev/null 2>&1; then
        timeout --kill-after=1s 15s pw-play "$1" </dev/null >/dev/null 2>&1 &
        disown "$!" 2>/dev/null || true
    elif command -v paplay >/dev/null 2>&1; then
        timeout --kill-after=1s 15s paplay "$1" </dev/null >/dev/null 2>&1 &
        disown "$!" 2>/dev/null || true
    else
        printf 'dusky-glance: No pw-play or paplay for alarm sound\n' >&2
    fi
    return 0
}
send_alert() {
    # The notification summary is plain text; only markup in the body needs escaping.
    if ! timeout --kill-after=1s 3s notify-send -u critical \
        -a dusky-glance-alert \
        -h "string:x-canonical-private-synchronous:${1}-${MY_PID}" \
        -- "$2" >/dev/null 2>&1; then
        printf 'dusky-glance: Critical alert delivery failed: %s\n' "$2" >&2
    fi
}
flash_finish() {
    local i
    for ((i = 0; i < 3; i++)); do
        send_osd '00:00'
        pause_for 0.4
        send_osd '     '
        pause_for 0.4
    done
}

# Scan real UTC minutes with local strftime. This handles DST gaps/folds without
# parsing ambiguous local timestamps or starting an external process per minute.
next_alarm_epoch() {
    local -n _dest=$1
    local _now=$EPOCHSECONDS _probe _text _seconds _n
    printf -v _text '%(%H:%M)T' "$_now"
    printf -v _seconds '%(%S)T' "$_now"
    if [[ "$_text" == "$2" && "$_seconds" == 00 ]]; then
        _dest=$_now
        return 0
    fi
    _probe=$(( (_now / 60 + 1) * 60 ))
    for ((_n = 0; _n < 4320; _n++)); do
        printf -v _text '%(%H:%M)T' "$_probe"
        if [[ "$_text" == "$2" ]]; then
            _dest=$_probe
            return 0
        fi
        _probe=$((_probe + 60))
    done
    return 1
}
offset_to_minutes() {
    local -n _dest=$1
    local _s=$2 _h _m
    [[ "$_s" =~ ^[+-][0-9]{4}$ ]] || return 1
    _h=$((10#${_s:1:2}))
    _m=$((10#${_s:3:2}))
    (( _h <= 23 && _m <= 59 )) || return 1
    _dest=$((_h * 60 + _m))
    [[ "${_s:0:1}" == - ]] && _dest=$((-_dest))
    return 0
}

find_system_battery() {
    local candidate kind scope present
    for candidate in /sys/class/power_supply/*; do
        [[ -d "$candidate" ]] || continue
        kind="" scope="" present="1"
        { read -r kind < "$candidate/type"; } 2>/dev/null || continue
        [[ "$kind" == Battery ]] || continue
        { read -r scope < "$candidate/scope"; } 2>/dev/null || true
        [[ "$scope" == Device ]] && continue
        { read -r present < "$candidate/present"; } 2>/dev/null || true
        [[ "$present" == 0 ]] && continue
        printf '%s\n' "$candidate"
        return 0
    done
    return 1
}
find_cpu_temp_sensor() {
    local wanted f name dir label text
    for wanted in coretemp k10temp zenpower cpu_thermal; do
        for f in /sys/class/hwmon/hwmon*/name; do
            { read -r name < "$f"; } 2>/dev/null || continue
            [[ "$name" == "$wanted" ]] || continue
            dir="${f%/*}"
            if [[ "$wanted" == k10temp ]]; then
                for label in "$dir"/temp*_label; do
                    { read -r text < "$label"; } 2>/dev/null || continue
                    if [[ "$text" == Tdie && -r "${label%_label}_input" ]]; then
                        printf '%s\n' "${label%_label}_input"
                        return 0
                    fi
                done
            fi
            if [[ -r "$dir/temp1_input" ]]; then
                printf '%s\n' "$dir/temp1_input"
                return 0
            fi
        done
    done
    for f in /sys/class/thermal/thermal_zone*/type; do
        { read -r name < "$f"; } 2>/dev/null || continue
        if [[ "$name" == *x86_pkg_temp* || "$name" == *cpu* ]]; then
            [[ -r "${f%/*}/temp" ]] || continue
            printf '%s\n' "${f%/*}/temp"
            return 0
        fi
    done
    return 1
}
find_rapl_domain() {
    local f name energy inaccessible=""
    # Powercap exposes packages and subzones as class entries. Prefer MSR
    # over MMIO: both interfaces may report the same package's energy.
    for f in /sys/class/powercap/intel-rapl:*/name \
             /sys/class/powercap/intel-rapl-mmio:*/name; do
        { read -r name < "$f"; } 2>/dev/null || continue
        case "$1:$name" in
            package:package-*|uncore:uncore)
                energy="${f%/*}/energy_uj"
                if [[ -r "$energy" ]]; then
                    printf '%s\n' "$energy"
                    return 0
                elif [[ -e "$energy" && -z "$inaccessible" ]]; then
                    inaccessible=$energy
                fi ;;
        esac
    done
    # Retain an existing, inaccessible sensor so callers can distinguish a
    # permission problem from hardware without an energy counter.
    if [[ -n "$inaccessible" ]]; then
        printf '%s\n' "$inaccessible"
        return 0
    fi
    return 1
}
find_intel_gpu_energy() {
    local dir name
    for dir in /sys/class/drm/"$1"/device/hwmon/hwmon*/ \
               /sys/class/drm/"$1"/device/hwmon*/; do
        { read -r name < "${dir}name"; } 2>/dev/null || continue
        [[ "$name" == i915 || "$name" == xe ]] || continue
        if [[ -r "${dir}energy1_input" ]]; then
            printf '%s\n' "${dir}energy1_input"
            return 0
        fi
    done
    return 1
}
find_amd_power() {
    local f
    for f in /sys/class/drm/"$1"/device/hwmon/hwmon*/power1_average \
             /sys/class/drm/"$1"/device/hwmon*/power1_average \
             /sys/class/drm/"$1"/device/hwmon/hwmon*/power1_input \
             /sys/class/drm/"$1"/device/hwmon*/power1_input; do
        if [[ -r "$f" ]]; then printf '%s\n' "$f"; return 0; fi
    done
    return 1
}
find_gpu_temp_sensors() {
    local f
    for f in /sys/class/drm/"$1"/device/hwmon/hwmon*/temp*_input \
             /sys/class/drm/"$1"/device/hwmon*/temp*_input; do
        [[ -r "$f" ]] && printf '%s\n' "$f"
    done
    return 0
}
find_intel_idle_sensor() {
    local f
    for f in /sys/class/drm/"$1"/power/rc6_residency_ms \
             /sys/class/drm/"$1"/gt/gt0/rc6_residency_ms \
             /sys/class/drm/"$1"/device/drm/"$1"/power/rc6_residency_ms \
             /sys/class/drm/"$1"/device/tile0/gt0/gtidle/idle_residency_ms; do
        if [[ -r "$f" ]]; then printf '%s\n' "$f"; return 0; fi
    done
    return 1
}
find_disk_temp_sensors() {
    local dev="$1" ctrl="" f real
    local -A seen=()
    for f in /sys/class/block/"$dev"/device/hwmon*/temp*_input \
             /sys/class/block/"$dev"/device/hwmon/hwmon*/temp*_input; do
        [[ -r "$f" ]] || continue
        real=$(readlink -f -- "$f") || continue
        [[ -z "${seen[$real]:-}" ]] || continue
        seen["$real"]=1
        printf '%s\n' "$f"
    done
    if [[ "$dev" =~ ^(nvme[0-9]+) ]]; then
        ctrl="${BASH_REMATCH[1]}"
        for f in /sys/class/nvme/"$ctrl"/hwmon*/temp*_input \
                 /sys/class/nvme/"$ctrl"/hwmon/hwmon*/temp*_input \
                 /sys/class/nvme/"$ctrl"/device/hwmon*/temp*_input \
                 /sys/class/nvme/"$ctrl"/device/hwmon/hwmon*/temp*_input; do
            [[ -r "$f" ]] || continue
            real=$(readlink -f -- "$f") || continue
            [[ -z "${seen[$real]:-}" ]] || continue
            seen["$real"]=1
            printf '%s\n' "$f"
        done
    fi
    return 0
}

declare -A ENERGY_LAST=() ENERGY_TIME=()
sample_energy_watts() {
    local -n _dest=$1
    local _path="$2" _range_file="${3:-}" _energy _now _delta _dt _range=0 _tenths
    _dest=N/A
    if ! read_uint _energy "$_path" || ! boottime_us _now; then
        unset "ENERGY_LAST[$_path]" "ENERGY_TIME[$_path]"
        return 0
    fi
    if [[ -n "${ENERGY_LAST[$_path]+x}" ]]; then
        _delta=$((_energy - ENERGY_LAST[$_path]))
        _dt=$((_now - ENERGY_TIME[$_path]))
        if (( _delta < 0 )) && [[ -n "$_range_file" ]] &&
           read_uint _range "$_range_file" &&
           (( _range > 0 && _energy < _range && ENERGY_LAST[$_path] < _range )); then
            _delta=$((_delta + _range))
        fi
        if (( _dt > 0 && _dt <= 5000000 && _delta >= 0 &&
              _delta <= _dt * 5000 )); then
            _tenths=$(((_delta * 10 + _dt / 2) / _dt))
            _dest="$((_tenths / 10)).$((_tenths % 10))W"
        fi
    fi
    ENERGY_LAST["$_path"]=$_energy
    ENERGY_TIME["$_path"]=$_now
    return 0
}

CPU_PREV_IDLE=-1 CPU_PREV_TOTAL=-1
CPU_POWER_PATH="" CPU_POWER_LAST=-5
CPU_POWER_WARNED=0
CPU_TEMP_PATH="" CPU_TEMP_LAST=-5
cpu_usage_once() {
    local -n _dest=$1
    local _tag _user _nice _system _idle _iowait _irq _softirq _steal
    local _total _idle_all _diff_total _diff_idle _usage
    _dest=N/A
    if { read -r _tag _user _nice _system _idle _iowait _irq _softirq _steal _ < /proc/stat; } 2>/dev/null &&
       [[ "$_tag" == cpu && "$_user" =~ ^[0-9]+$ && "$_nice" =~ ^[0-9]+$ &&
          "$_system" =~ ^[0-9]+$ && "$_idle" =~ ^[0-9]+$ &&
          "$_iowait" =~ ^[0-9]+$ && "$_irq" =~ ^[0-9]+$ &&
          "$_softirq" =~ ^[0-9]+$ && "$_steal" =~ ^[0-9]+$ ]]; then
        _idle_all=$((_idle + _iowait))
        _total=$((_user + _nice + _system + _idle + _iowait + _irq + _softirq + _steal))
        _diff_total=$((_total - CPU_PREV_TOTAL))
        _diff_idle=$((_idle_all - CPU_PREV_IDLE))
        if (( CPU_PREV_TOTAL >= 0 && _diff_total > 0 )); then
            # Linux permits iowait to decrease. Like btop, clamp the idle
            # delta and round the percentage instead of dropping the sample.
            (( _diff_idle < 0 )) && _diff_idle=0
            (( _diff_idle > _diff_total )) && _diff_idle=$_diff_total
            _usage=$(((100 * (_diff_total - _diff_idle) + _diff_total / 2) / _diff_total))
            _dest="${_usage}%"
        fi
        CPU_PREV_IDLE=$_idle_all CPU_PREV_TOTAL=$_total
    else
        CPU_PREV_IDLE=-1 CPU_PREV_TOTAL=-1
    fi
    return 0
}
cpu_power_once() {
    local -n _dest=$1
    _dest=N/A
    if [[ -z "$CPU_POWER_PATH" || ! -r "$CPU_POWER_PATH" ]]; then
        if (( SECONDS - CPU_POWER_LAST >= 5 )); then
            CPU_POWER_LAST=$SECONDS
            CPU_POWER_PATH=$(find_rapl_domain package || true)
        fi
    fi
    if [[ -n "$CPU_POWER_PATH" ]]; then
        if [[ -r "$CPU_POWER_PATH" ]]; then
            CPU_POWER_WARNED=0
            sample_energy_watts "$1" "$CPU_POWER_PATH" "${CPU_POWER_PATH%/*}/max_energy_range_uj"
        elif [[ -e "$CPU_POWER_PATH" ]]; then
            _dest='No access'
            if (( CPU_POWER_WARNED == 0 )); then
                printf 'dusky-glance: Cannot read CPU energy counter: %s; enable/start glance_cpu_pkg_watt.service\n' \
                    "$CPU_POWER_PATH" >&2
                CPU_POWER_WARNED=1
            fi
        fi
    else
        CPU_POWER_WARNED=0
    fi
    return 0
}
cpu_temp_once() {
    local -n _dest=$1
    _dest=N/A
    if [[ -z "$CPU_TEMP_PATH" || ! -r "$CPU_TEMP_PATH" ]]; then
        CPU_TEMP_PATH=""
        if (( SECONDS - CPU_TEMP_LAST >= 5 )); then
            CPU_TEMP_LAST=$SECONDS
            CPU_TEMP_PATH=$(find_cpu_temp_sensor || true)
        fi
    fi
    if [[ -n "$CPU_TEMP_PATH" ]]; then
        read_temp_c "$1" "$CPU_TEMP_PATH" || CPU_TEMP_PATH=""
    fi
    return 0
}
ram_once() {
    local -n _dest=$1
    local key val total="" avail="" free="" cached=""
    _dest=N/A
    if ! {
        while read -r key val _; do
            case "$key" in
                MemTotal:) total=$val ;;
                MemAvailable:) avail=$val ;;
                MemFree:) free=$val ;;
                Cached:) cached=$val ;;
            esac
            [[ -n "$total" && -n "$avail" ]] && break
        done < /proc/meminfo
    } 2>/dev/null; then
        return 0
    fi
    if [[ -z "$avail" && "$free" =~ ^[0-9]+$ && "$cached" =~ ^[0-9]+$ ]]; then
        avail=$((free + cached))
    fi
    if [[ "$total" =~ ^[0-9]+$ && "$avail" =~ ^[0-9]+$ ]] &&
       (( total > 0 && avail <= total )); then
        _dest="$(((total - avail) / 1024))"
    fi
    return 0
}
ram_available_once() {
    local -n _dest=$1
    local key val total="" avail="" free="" cached=""
    _dest=N/A
    if ! {
        while read -r key val _; do
            case "$key" in
                MemTotal:) total=$val ;;
                MemAvailable:) avail=$val; break ;;
                MemFree:) free=$val ;;
                Cached:) cached=$val ;;
            esac
        done < /proc/meminfo
    } 2>/dev/null; then
        return 0
    fi
    if [[ -z "$avail" && "$free" =~ ^[0-9]+$ && "$cached" =~ ^[0-9]+$ ]]; then
        avail=$((free + cached))
    fi
    if [[ "$avail" =~ ^[0-9]+$ ]] &&
       { [[ -z "$total" ]] || (( total <= 0 || avail <= total )); }; then
        _dest="$((avail / 1024))"
    fi
    return 0
}
ram_free_once() {
    local -n _dest=$1
    local key val total="" free=""
    _dest=N/A
    if ! {
        while read -r key val _; do
            case "$key" in
                MemTotal:) total=$val ;;
                MemFree:) free=$val; break ;;
            esac
        done < /proc/meminfo
    } 2>/dev/null; then
        return 0
    fi
    if [[ "$free" =~ ^[0-9]+$ ]] &&
       { [[ -z "$total" ]] || (( total <= 0 || free <= total )); }; then
        _dest="$((free / 1024))"
    fi
    return 0
}

GPU_CARD="" GPU_VENDOR="" GPU_PDEV="" NVIDIA_PCI_ID=""
GPU_POWER_PATH="" GPU_POWER_KIND="" GPU_POWER_LAST=-5
GPU_IDLE_PATH="" GPU_IDLE_DISCOVER=-5
GPU_RC6_LAST=-1 GPU_RC6_TIME=0
GPU_MEM_LAST=-15 GPU_MEM_CACHE=N/A
GPU_TEMP_LAST=-5
GPU_NV_SEC=-1 GPU_NV_POWER=N/A GPU_NV_USAGE=N/A GPU_NV_MEM=N/A GPU_NV_TEMP=N/A
GPU_TEMP_FILES=()

if [[ "$MODE" == --gpu-power || "$MODE" == --gpu-usage ||
      "$MODE" == --gpu-mem || ( "$MODE" == --hud && $# == 3 ) ]]; then
    GPU_CARD="$2" GPU_VENDOR="$3"
    resolved=$(readlink -f -- "/sys/class/drm/$GPU_CARD/device") ||
        die "Cannot resolve PCI device for $GPU_CARD"
    GPU_PDEV="${resolved##*/}"
    if [[ "$GPU_VENDOR" == nvidia ]]; then
        [[ "$GPU_PDEV" =~ ^[[:xdigit:]]{4}:[[:xdigit:]]{2}:[[:xdigit:]]{2}\.[0-7]$ ]] ||
            die "Invalid NVIDIA PCI device: $GPU_PDEV"
        NVIDIA_PCI_ID=$GPU_PDEV
    fi
fi

is_nvidia_suspended() {
    local _state="" _runtime="" _dir="/sys/class/drm/$GPU_CARD/device"
    { read -r _state < "$_dir/power_state"; } 2>/dev/null || true
    { read -r _runtime < "$_dir/power/runtime_status"; } 2>/dev/null || true
    [[ "$_state" == D3* || "$_runtime" == suspended ]]
}
query_nvidia() {
    timeout --kill-after=1s 3s nvidia-smi \
        --id="$NVIDIA_PCI_ID" \
        --query-gpu=power.draw,utilization.gpu,memory.used,temperature.gpu \
        --format=csv,noheader,nounits 2>/dev/null
}
nvidia_snapshot() {
    local line="" power="" usage="" memory="" temp="" whole frac
    (( SECONDS - GPU_NV_SEC >= 2 )) || return 0
    GPU_NV_SEC=$SECONDS
    GPU_NV_POWER=N/A GPU_NV_USAGE=N/A GPU_NV_MEM=N/A GPU_NV_TEMP=N/A
    if is_nvidia_suspended; then
        GPU_NV_POWER=D3 GPU_NV_USAGE=D3 GPU_NV_MEM=D3 GPU_NV_TEMP=D3
        return 0
    fi
    if ! line=$(query_nvidia); then
        GPU_NV_SEC=$SECONDS
        return 0
    fi
    IFS=',' read -r power usage memory temp <<< "$line"
    power="${power//[[:space:]]/}" usage="${usage//[[:space:]]/}"
    memory="${memory//[[:space:]]/}" temp="${temp//[[:space:]]/}"
    if [[ "$power" =~ ^[0-9]+(\.[0-9]+)?$ ]]; then
        whole="${power%%.*}"
        frac=0
        [[ "$power" == *.* ]] && frac="${power#*.}"
        GPU_NV_POWER="${whole}.${frac:0:1}W"
    fi
    if [[ "$usage" =~ ^[0-9]{1,3}$ ]] && (( 10#$usage <= 100 )); then
        GPU_NV_USAGE="$((10#$usage))%"
    fi
    if [[ "$memory" =~ ^[0-9]{1,12}$ ]]; then
        GPU_NV_MEM="$((10#$memory))MB"
    fi
    if [[ "$temp" =~ ^[0-9]{1,3}$ ]]; then
        GPU_NV_TEMP="$((10#$temp))°C"
    fi
    GPU_NV_SEC=$SECONDS
    return 0
}

gpu_power_once() {
    local -n _dest=$1
    local _raw _tenths
    _dest=N/A
    case "$GPU_VENDOR" in
        intel|amd)
            if [[ -z "$GPU_POWER_PATH" || ! -r "$GPU_POWER_PATH" ]]; then
                GPU_POWER_PATH="" GPU_POWER_KIND=""
                if (( SECONDS - GPU_POWER_LAST >= 5 )); then
                    GPU_POWER_LAST=$SECONDS
                    if [[ "$GPU_VENDOR" == intel ]]; then
                        GPU_POWER_PATH=$(find_intel_gpu_energy "$GPU_CARD" || true)
                        GPU_POWER_KIND=device
                        if [[ -z "$GPU_POWER_PATH" && "$GPU_PDEV" == 0000:00:02.0 ]]; then
                            GPU_POWER_PATH=$(find_rapl_domain uncore || true)
                            GPU_POWER_KIND=uncore
                        fi
                    else
                        GPU_POWER_PATH=$(find_amd_power "$GPU_CARD" || true)
                    fi
                fi
            fi
            if [[ -n "$GPU_POWER_PATH" ]]; then
                if [[ "$GPU_VENDOR" == intel ]]; then
                    if [[ "$GPU_POWER_KIND" == uncore ]]; then
                        sample_energy_watts "$1" "$GPU_POWER_PATH" \
                            "${GPU_POWER_PATH%/*}/max_energy_range_uj"
                        [[ "$_dest" == N/A ]] || _dest="U ${_dest}"
                    else
                        sample_energy_watts "$1" "$GPU_POWER_PATH"
                    fi
                elif read_uint _raw "$GPU_POWER_PATH" && (( _raw <= 10000000000 )); then
                    _tenths=$(((_raw + 50000) / 100000))
                    _dest="$((_tenths / 10)).$((_tenths % 10))W"
                fi
            fi ;;
        nvidia)
            nvidia_snapshot
            _dest=$GPU_NV_POWER ;;
    esac
    return 0
}
gpu_usage_once() {
    local -n _dest=$1
    local _path _rc6 _now _delta _ms _use
    _dest=N/A
    case "$GPU_VENDOR" in
        intel)
            if [[ -z "$GPU_IDLE_PATH" || ! -r "$GPU_IDLE_PATH" ]]; then
                GPU_IDLE_PATH=""
                GPU_RC6_LAST=-1
                if (( SECONDS - GPU_IDLE_DISCOVER >= 5 )); then
                    GPU_IDLE_DISCOVER=$SECONDS
                    GPU_IDLE_PATH=$(find_intel_idle_sensor "$GPU_CARD" || true)
                fi
            fi
            _path=$GPU_IDLE_PATH
            if [[ -r "$_path" ]] && read_uint _rc6 "$_path" && boottime_us _now; then
                if (( GPU_RC6_LAST >= 0 )); then
                    _delta=$((_rc6 - GPU_RC6_LAST))
                    _ms=$(((_now - GPU_RC6_TIME) / 1000))
                    if (( _ms > 0 && _ms <= 5000 && _delta >= 0 && _delta <= _ms + 50 )); then
                        if (( _delta > _ms )); then
                            _use=0
                        else
                            _use=$((100 * (_ms - _delta) / _ms))
                        fi
                        _dest="${_use}%"
                    fi
                fi
                GPU_RC6_LAST=$_rc6 GPU_RC6_TIME=$_now
            else
                GPU_RC6_LAST=-1
            fi ;;
        amd)
            _path="/sys/class/drm/$GPU_CARD/device/gpu_busy_percent"
            if read_uint _use "$_path" 3 && (( _use <= 100 )); then
                _dest="${_use}%"
            fi ;;
        nvidia)
            nvidia_snapshot
            _dest=$GPU_NV_USAGE ;;
    esac
    return 0
}

# fdinfo counts client allocations, not unique physical pages. Dedup duplicated
# descriptors by DRM client ID and avoid mixing this card with other PCI GPUs.
intel_memory_mib() {
    local -n _dest=$1
    local target="$2" file field="" val="" unit="" region group factor bytes
    local driver client pdev found invalid indexed any=false total sum=0 key
    local -A clients=()
    local -A base=() numbered=() counted=()
    _dest=""
    [[ -n "$target" ]] || return 1
    for file in /proc/[0-9]*/fdinfo/*; do
        [[ -r "$file" ]] || continue
        driver="" client="" pdev="" found=false invalid=false
        base=() numbered=() counted=()
        while read -r field val unit || [[ -n "$field" ]]; do
            case "$field" in
                drm-driver:) driver=$val ;;
                drm-client-id:) client=$val ;;
                drm-pdev:) pdev=$val ;;
                *)
                    [[ "$field" =~ ^drm-total-([a-z][a-z0-9_]*):$ ]] || continue
                    region="${BASH_REMATCH[1]}" indexed=false
                    if [[ "$region" =~ ^([a-z_]+)([0-9]+)$ ]]; then
                        region="${BASH_REMATCH[1]}"
                        indexed=true
                    fi
                    [[ "$val" =~ ^[0-9]{1,12}$ ]] || continue
                    case "$unit" in
                        B|'') factor=1 ;;
                        KiB) factor=1024 ;;
                        MiB) factor=1048576 ;;
                        GiB) factor=1073741824 ;;
                        *) continue ;;
                    esac
                    (( 10#$val <= 1000000000000000 / factor )) || continue
                    bytes=$((10#$val * factor))
                    found=true
                    if [[ "$indexed" == true ]]; then
                        if (( bytes > 1000000000000000 - ${numbered[$region]:-0} )); then
                            invalid=true
                            break
                        fi
                        numbered["$region"]=$((${numbered[$region]:-0} + bytes))
                    else
                        base["$region"]=$bytes
                    fi ;;
            esac
        done < "$file" 2>/dev/null || continue
        if [[ "$driver" =~ ^(i915|xe)$ && "$client" =~ ^[0-9]+$ &&
              "$pdev" == "$target" && "$found" == true && "$invalid" == false ]]; then
            total=0
            for group in "${!base[@]}" "${!numbered[@]}"; do
                [[ -z "${counted[$group]+x}" ]] || continue
                counted["$group"]=1
                bytes=${numbered[$group]:-${base[$group]:-0}}
                if (( bytes > 1000000000000000 - total )); then
                    invalid=true
                    break
                fi
                total=$((total + bytes))
            done
            [[ "$invalid" == false ]] || continue
            key="${driver}_${pdev}_${client}"
            if [[ -z "${clients[$key]+x}" ]] || (( total > clients[$key] )); then
                clients["$key"]=$total
            fi
            any=true
        fi
    done
    [[ "$any" == true ]] || return 1
    for key in "${!clients[@]}"; do
        (( clients[$key] <= 1000000000000000 - sum )) || return 1
        sum=$((sum + clients[$key]))
    done
    _dest=$((sum / 1048576))
    return 0
}
gpu_mem_once() {
    local -n _dest=$1
    local _mib _bytes
    _dest=N/A
    case "$GPU_VENDOR" in
        intel)
            if (( SECONDS - GPU_MEM_LAST >= 15 )); then
                GPU_MEM_LAST=$SECONDS
                if intel_memory_mib _mib "$GPU_PDEV"; then
                    GPU_MEM_CACHE="${_mib}MB"
                else
                    GPU_MEM_CACHE=N/A
                fi
            fi
            _dest=$GPU_MEM_CACHE ;;
        amd)
            if read_uint _bytes "/sys/class/drm/$GPU_CARD/device/mem_info_vram_used"; then
                _dest="$((_bytes / 1048576))MB"
            fi ;;
        nvidia)
            nvidia_snapshot
            _dest=$GPU_NV_MEM ;;
    esac
    return 0
}
gpu_temp_once() {
    local -n _dest=$1
    local period
    _dest=N/A
    if [[ "$GPU_VENDOR" == nvidia ]]; then
        # Query once via NVML, without touching hwmon on a suspended dGPU.
        nvidia_snapshot
        _dest=$GPU_NV_TEMP
        return 0
    fi
    period=15
    (( ${#GPU_TEMP_FILES[@]} == 0 )) && period=5
    if (( SECONDS - GPU_TEMP_LAST >= period )); then
        GPU_TEMP_LAST=$SECONDS
        mapfile -t GPU_TEMP_FILES < <(find_gpu_temp_sensors "$GPU_CARD")
    fi
    if (( ${#GPU_TEMP_FILES[@]} > 0 )); then
        if read_temp_c "$1" "${GPU_TEMP_FILES[0]}"; then return 0; fi
        GPU_TEMP_FILES=()
    fi
    return 0
}

battery_full() {
    local -n _dest=$1
    if read_uint "$1" "$2/${3}_full" 12 && (( _dest > 0 )); then return 0; fi
    read_uint "$1" "$2/${3}_full_design" 12 && (( _dest > 0 ))
}
micro_product() {
    local -n _dest=$1
    local quantity=$2 volts=$3
    # uAh*uV -> uWh, or uA*uV -> uW; split the product to avoid overflow.
    # shellcheck disable=SC2017
    _dest=$((quantity / 1000000 * volts + (quantity % 1000000) * volts / 1000000))
}
format_hours_minutes() {
    local -n _dest=$1
    printf -v _dest '%dh%dm' "$(($2 / 60))" "$(($2 % 60))"
}

case "$MODE" in
    --clock)
        while true; do
            printf -v current_time '%(%I:%M:%S)T' -1
            send_osd "$current_time"
            wait_tick
        done ;;

    --clock-short)
        last_clock_value="" last_clock_push=-10
        while true; do
            printf -v current_time '%(%I:%M)T' -1
            if [[ "$current_time" != "$last_clock_value" ]] ||
               (( SECONDS - last_clock_push >= 10 )); then
                send_osd "$current_time"
                last_clock_value=$current_time last_clock_push=$SECONDS
            fi
            delay=$((60 - EPOCHSECONDS % 60))
            (( delay > 10 )) && delay=10
            (( delay < 1 )) && delay=1
            pause_for "$delay"
        done ;;

    --world-clock)
        tz_name="$2" place_label="$3" local_tz="${TZ-:/etc/localtime}"
        while true; do
            now=$EPOCHSECONDS
            if ! { TZ="$local_tz" printf -v local_offset '%(%z)T' "$now" &&
                   TZ="$tz_name" printf -v target_data '%(%I:%M:%S %p|%z)T' "$now"; }; then
                send_osd N/A
                wait_tick
                continue
            fi
            time_str="${target_data%|*}"
            target_offset="${target_data##*|}"
            local_min=0 target_min=0
            if offset_to_minutes local_min "$local_offset" &&
               offset_to_minutes target_min "$target_offset"; then
                diff=$((target_min - local_min))
                if (( diff == 0 )); then
                    diff_label='same time'
                else
                    sign=+
                    if (( diff < 0 )); then sign=-; diff=$((-diff)); fi
                    if (( diff % 60 == 0 )); then
                        diff_label="${sign}$((diff / 60))h"
                    else
                        diff_label="${sign}$((diff / 60))h$((diff % 60))m"
                    fi
                fi
            else
                diff_label=N/A
            fi
            send_world_clock_osd "$time_str" "$place_label" "$diff_label"
            wait_tick
        done ;;

    --stopwatch)
        boottime_us start_us || die 'Cannot read /proc/uptime'
        while true; do
            if boottime_us now_us && (( now_us >= start_us )); then
                elapsed=$(((now_us - start_us) / 1000000))
                format_time time_str "$elapsed"
                send_osd "$time_str"
            else
                send_osd N/A
            fi
            wait_tick
        done ;;

    --timer)
        boottime_us start_us || die 'Cannot read /proc/uptime'
        target_us=$((start_us + $2 * 1000000))
        while true; do
            if ! boottime_us now_us; then
                send_osd N/A
                wait_tick
                continue
            fi
            if (( now_us >= target_us )); then
                send_alert dusky-timer-alert "󰔛  Time's Up!"
                play_sound '/usr/share/sounds/freedesktop/stereo/alarm-clock-elapsed.oga'
                flash_finish
                exit 0
            fi
            left=$(((target_us - now_us + 999999) / 1000000))
            format_time time_str "$left"
            send_osd "$time_str"
            wait_tick
        done ;;

    --alarm)
        next_alarm_epoch target_epoch "$2" || die 'Could not find the next local alarm time'
        while true; do
            now=$EPOCHSECONDS
            if (( now >= target_epoch )); then
                send_alert dusky-alarm-alert "Alarm: $3"
                play_sound '/usr/share/sounds/freedesktop/stereo/alarm-clock-elapsed.oga'
                flash_finish
                exit 0
            fi
            left=$((target_epoch - now))
            alarm_mins=$(((left + 59) / 60))
            printf -v time_str '%02d:%02d' "$((alarm_mins / 60))" "$((alarm_mins % 60))"
            send_osd "$time_str"
            wait_tick
        done ;;

    --pomodoro)
        boottime_us start_us || die 'Cannot read /proc/uptime'
        phase=WORK
        target_us=$((start_us + $2 * 1000000))
        while true; do
            if ! boottime_us now_us; then
                send_osd N/A
                wait_tick
                continue
            fi
            if (( now_us >= target_us )); then
                if [[ "$phase" == WORK ]] && (( $3 > 0 )); then
                    send_alert dusky-pomo-alert "󰦖  Break Time!"
                    play_sound '/usr/share/sounds/gnome/default/alarms/glass-bell.oga'
                    phase=BREAK
                    boottime_us now_us || die 'Cannot read /proc/uptime'
                    target_us=$((now_us + $3 * 1000000))
                else
                    message='Session Finished'
                    (( $3 > 0 )) && message='Back to Work!'
                    send_alert dusky-pomo-alert "󰔚  $message"
                    play_sound '/usr/share/sounds/freedesktop/stereo/alarm-clock-elapsed.oga'
                    phase=WORK
                    boottime_us now_us || die 'Cannot read /proc/uptime'
                    target_us=$((now_us + $2 * 1000000))
                fi
                continue
            fi
            left=$(((target_us - now_us + 999999) / 1000000))
            format_time time_str "$left"
            if [[ "$phase" == BREAK ]]; then time_str="B $time_str"; fi
            send_osd "$time_str"
            wait_tick
        done ;;

    --cpu-power)
        cpu_power_once initial
        wait_tick
        while true; do
            cpu_power_once reading
            send_osd "$reading"
            wait_tick
        done ;;

    --cpu)
        cpu_usage_once initial
        wait_tick
        while true; do
            cpu_usage_once reading
            send_osd "$reading"
            wait_tick
        done ;;

    --ram)
        while true; do
            ram_once reading
            send_osd "$reading"
            wait_tick
        done ;;

    --ram-available)
        while true; do
            ram_available_once reading
            send_osd "$reading"
            wait_tick
        done ;;

    --ram-free)
        while true; do
            ram_free_once reading
            send_osd "$reading"
            wait_tick
        done ;;

    --ram-temp)
        temp_files=() last_discover=-5
        while true; do
            period=15
            (( ${#temp_files[@]} == 0 )) && period=5
            if (( SECONDS - last_discover >= period )); then
                last_discover=$SECONDS
                temp_files=()
                for dir in /sys/class/hwmon/hwmon*/; do
                    name=""
                    { read -r name < "${dir}name"; } 2>/dev/null || continue
                    if [[ "$name" == spd5118 || "$name" == jc42 ]]; then
                        if [[ -r "${dir}temp1_input" ]]; then
                            temp_files+=("${dir}temp1_input")
                        fi
                    fi
                done
            fi
            temps=()
            for sensor in "${temp_files[@]}"; do
                if read_temp_c temperature "$sensor"; then temps+=("${temperature%°C}°"); fi
            done
            if (( ${#temps[@]} > 0 )); then
                send_osd "${temps[*]}"
            else
                temp_files=()
                send_osd N/A
            fi
            wait_tick
        done ;;

    --zram)
        while true; do
            if [[ -r /sys/block/zram0/mm_stat ]] &&
               read -r original compressed memory_used _ < /sys/block/zram0/mm_stat &&
               [[ "$original" =~ ^[0-9]+$ && "$compressed" =~ ^[0-9]+$ &&
                  "$memory_used" =~ ^[0-9]+$ ]]; then
                reading="$((memory_used / 1048576))MB"
                if (( compressed > 0 )); then
                    reading+=" $((original / compressed)):1"
                fi
                send_osd "$reading"
            else
                send_osd N/A
            fi
            wait_tick
        done ;;

    --temp)
        while true; do
            cpu_temp_once reading
            send_osd "$reading"
            wait_tick
        done ;;

    --battery|--battery-percent|--battery-watts|--battery-time)
        bat_dir="" bat_last=-5
        while true; do
            present=1
            if [[ -n "$bat_dir" && -d "$bat_dir" ]]; then
                { read -r present < "$bat_dir/present"; } 2>/dev/null || present=1
                [[ "$present" == 0 ]] && bat_dir=""
            else
                bat_dir=""
            fi
            if [[ -z "$bat_dir" ]] && (( SECONDS - bat_last >= 5 )); then
                bat_last=$SECONDS
                bat_dir=$(find_system_battery || true)
            fi
            if [[ -z "$bat_dir" ]]; then
                send_osd 'Bat: N/A'
                wait_tick
                continue
            fi

            capacity='?'
            if read_uint percentage "$bat_dir/capacity" 3 && (( percentage <= 100 )); then
                capacity=$percentage
            elif read_uint energy_now "$bat_dir/energy_now" 12 &&
                 battery_full energy_full "$bat_dir" energy; then
                capacity=$(((energy_now * 100 + energy_full / 2) / energy_full))
            elif read_uint charge_now "$bat_dir/charge_now" 12 &&
                 battery_full charge_full "$bat_dir" charge; then
                capacity=$(((charge_now * 100 + charge_full / 2) / charge_full))
            fi
            if [[ "$capacity" != '?' ]] && (( capacity > 100 )); then capacity=100; fi
            if [[ "$MODE" == --battery-percent ]]; then
                if [[ "$capacity" == '?' ]]; then send_osd 'Bat: N/A'
                else send_osd "${capacity}%"; fi
                wait_tick
                continue
            fi

            status=Unknown
            { read -r status < "$bat_dir/status"; } 2>/dev/null || status=Unknown
            volts=0 have_volts=false
            for field in voltage_now voltage_avg voltage_min_design voltage_max_design; do
                if read_abs candidate "$bat_dir/$field" &&
                   (( candidate > 0 && candidate <= 1000000000 )); then
                    volts=$candidate have_volts=true
                    break
                fi
            done
            power=0 have_power=false zero_power=false
            for field in power_now power_avg; do
                if read_abs candidate "$bat_dir/$field" &&
                   (( candidate <= 10000000000 )); then
                    if (( candidate > 0 )); then
                        power=$candidate have_power=true
                        break
                    fi
                    zero_power=true
                fi
            done
            current=0 have_current=false zero_current=false
            for field in current_now current_avg; do
                if read_abs candidate "$bat_dir/$field" &&
                   (( candidate <= 1000000000 )); then
                    if (( candidate > 0 )); then
                        current=$candidate have_current=true
                        break
                    fi
                    zero_current=true
                fi
            done
            if [[ "$have_power" == false && "$have_current" == true &&
                  "$have_volts" == true ]]; then
                micro_product power "$current" "$volts"
                have_power=true
            elif [[ "$have_power" == false ]] &&
                 { [[ "$zero_power" == true ]] ||
                   [[ "$zero_current" == true && "$have_volts" == true ]]; }; then
                have_power=true
            fi
            watts=N/A
            if [[ "$have_power" == true ]]; then
                tenths=$(((power + 50000) / 100000))
                watts="$((tenths / 10)).$((tenths % 10))W"
            fi
            if [[ "$MODE" == --battery-watts ]]; then
                send_osd "$watts"
                wait_tick
                continue
            fi

            minutes=-1
            if [[ "$status" == Discharging ]]; then
                for field in time_to_empty_now time_to_empty_avg; do
                    if read_uint seconds "$bat_dir/$field" 10 && (( seconds > 0 )); then
                        minutes=$(((seconds + 30) / 60))
                        break
                    fi
                done
                if (( minutes < 0 )); then
                    if [[ "$have_power" == true ]] && (( power > 0 )) &&
                       read_uint energy_now "$bat_dir/energy_now" 12; then
                        minutes=$(((energy_now * 60 + power / 2) / power))
                    elif [[ "$have_current" == true ]] &&
                         read_uint charge_now "$bat_dir/charge_now" 12; then
                        minutes=$(((charge_now * 60 + current / 2) / current))
                    elif [[ "$have_volts" == true && "$have_power" == true ]] &&
                         (( power > 0 )) && read_uint charge_now "$bat_dir/charge_now" 12; then
                        micro_product energy_now "$charge_now" "$volts"
                        minutes=$(((energy_now * 60 + power / 2) / power))
                    fi
                fi
            elif [[ "$status" == Charging ]]; then
                for field in time_to_full_now time_to_full_avg; do
                    if read_uint seconds "$bat_dir/$field" 10 && (( seconds > 0 )); then
                        minutes=$(((seconds + 30) / 60))
                        break
                    fi
                done
                if (( minutes < 0 )); then
                    if [[ "$have_power" == true ]] && (( power > 0 )) &&
                       read_uint energy_now "$bat_dir/energy_now" 12 &&
                       battery_full energy_full "$bat_dir" energy &&
                       (( energy_full > energy_now )); then
                        minutes=$((((energy_full - energy_now) * 60 + power / 2) / power))
                    elif [[ "$have_current" == true ]] &&
                         read_uint charge_now "$bat_dir/charge_now" 12 &&
                         battery_full charge_full "$bat_dir" charge &&
                         (( charge_full > charge_now )); then
                        minutes=$((((charge_full - charge_now) * 60 + current / 2) / current))
                    elif [[ "$have_volts" == true && "$have_power" == true ]] &&
                         (( power > 0 )) && read_uint charge_now "$bat_dir/charge_now" 12 &&
                         battery_full charge_full "$bat_dir" charge &&
                         (( charge_full > charge_now )); then
                        micro_product energy_left "$((charge_full - charge_now))" "$volts"
                        minutes=$(((energy_left * 60 + power / 2) / power))
                    fi
                fi
            fi

            # Unbounded EC estimates (for example a 0.001W denominator) are
            # not meaningful as a battery clock readout.
            if (( minutes > 43200 )); then minutes=-1; fi
            time_left=""
            if (( minutes >= 0 )); then
                format_hours_minutes time_left "$minutes"
            fi
            if [[ "$MODE" == --battery-time ]]; then
                if [[ -n "$time_left" ]]; then
                    send_osd "$time_left"
                elif [[ "$status" == Full ]]; then
                    send_osd Full
                elif [[ "$status" == 'Not charging' ]]; then
                    send_osd Held
                else
                    send_osd N/A
                fi
            elif [[ -n "$time_left" ]]; then
                send_osd "${capacity}% ${watts}"$'\n'"${time_left}"
            else
                send_osd "${capacity}% ${watts}"
            fi
            wait_tick
        done ;;

    --disk)
        TICK_PERIOD_US=5000000
        while true; do
            df_out=$(timeout --kill-after=1s 3s df -h --output=used,size,pcent / 2>/dev/null) || df_out=""
            row="${df_out##*$'\n'}"
            used="" size="" percent=""
            read -r used size percent <<< "$row"
            if [[ -n "$used" && -n "$size" && "$percent" == *% ]]; then
                send_osd "${used}/${size} ${percent}"
            else
                send_osd 'Disk: N/A'
            fi
            wait_tick
        done ;;

    --disk-read|--disk-write)
        stat_file="/sys/class/block/$2/stat"
        [[ -r "$stat_file" ]] || die "Cannot read block-device statistics: $2"
        field=2
        [[ "$MODE" == --disk-write ]] && field=6
        have_previous=false
        while true; do
            if ! { read -r -a stats < "$stat_file"; } 2>/dev/null ||
               (( ${#stats[@]} <= field )) ||
               [[ ! "${stats[field]}" =~ ^[0-9]{1,18}$ ]] ||
               ! boottime_us now_us; then
                have_previous=false
                send_osd N/A
                wait_tick
                continue
            fi
            sectors=$((10#${stats[field]}))
            if [[ "$have_previous" == true ]]; then
                delta=$((sectors - previous_sectors))
                interval=$((now_us - previous_time))
                if (( delta >= 0 && delta <= 1000000000 &&
                      interval > 0 && interval <= 5000000 )); then
                    tenths=$((delta * 10000000 / (2048 * interval)))
                    total_mib=$((sectors / 2048))
                    send_osd "${total_mib} $((tenths / 10)).$((tenths % 10))"
                else
                    send_osd N/A
                fi
            else
                send_osd Sampling
            fi
            previous_sectors=$sectors previous_time=$now_us
            have_previous=true
            wait_tick
        done ;;

    --disk-temp)
        TICK_PERIOD_US=5000000
        dev="$2"
        mapfile -t temp_files < <(find_disk_temp_sensors "$dev")
        last_discover=$SECONDS
        (( ${#temp_files[@]} > 0 )) || last_discover=-5
        cached_smart_temp="" last_smart_query=-15
        has_smart=false
        if command -v smartctl >/dev/null 2>&1 && command -v jq >/dev/null 2>&1; then
            has_smart=true
        fi
        while true; do
            period=15
            (( ${#temp_files[@]} == 0 )) && period=5
            if (( SECONDS - last_discover >= period )); then
                last_discover=$SECONDS
                mapfile -t temp_files < <(find_disk_temp_sensors "$dev")
            fi
            temps=()
            for sensor in "${temp_files[@]}"; do
                if read_temp_c temperature "$sensor"; then temps+=("${temperature%°C}°"); fi
            done
            if (( ${#temps[@]} > 0 )); then
                send_osd "${temps[*]}"
            else
                temp_files=()
                if [[ "$has_smart" == true ]]; then
                    if (( SECONDS - last_smart_query >= 15 )); then
                        last_smart_query=$SECONDS
                        candidate=""
                        smart_json=$(timeout --kill-after=1s 3s \
                            smartctl -n standby -Aj "/dev/$dev" 2>/dev/null || true)
                        if [[ -n "$smart_json" ]]; then
                            candidate=$(jq -er '.temperature.current | select(type == "number")' \
                                <<< "$smart_json" 2>/dev/null || true)
                        fi
                        if [[ -z "$candidate" ]] && command -v sudo >/dev/null 2>&1; then
                            smart_json=$(timeout --kill-after=1s 3s \
                                sudo -n smartctl -n standby -Aj "/dev/$dev" 2>/dev/null || true)
                            if [[ -n "$smart_json" ]]; then
                                candidate=$(jq -er '.temperature.current | select(type == "number")' \
                                    <<< "$smart_json" 2>/dev/null || true)
                            fi
                        fi
                        cached_smart_temp=""
                        if [[ "$candidate" =~ ^-?[0-9]{1,3}$ ]]; then
                            cached_smart_temp="${candidate}°"
                        fi
                    fi
                    send_osd "${cached_smart_temp:-N/A}"
                else
                    send_osd N/A
                fi
            fi
            wait_tick
        done ;;

    --network|--network-down|--network-up|--network-combined|\
    --network-down-session|--network-up-session|--network-session|\
    --network-boot-down|--network-boot-up|--network-boot)
        require_cmd systemctl
        NETWORK_BIN="$HOME/.local/bin/dusky-network"
        require_cmd "$NETWORK_BIN"
        case "$MODE_BASE" in
            network) network_mode=horizontal ;;
            network-down) network_mode=down ;;
            network-up) network_mode=up ;;
            network-combined) network_mode=combined ;;
            network-down-session) network_mode=session-down ;;
            network-up-session) network_mode=session-up ;;
            network-session) network_mode=session-total ;;
            network-boot-down) network_mode=boot-down ;;
            network-boot-up) network_mode=boot-up ;;
            network-boot) network_mode=boot-total ;;
        esac
        timeout --kill-after=1s 3s systemctl --user start dusky_network.service >/dev/null 2>&1 || true
        while true; do
            reading=$("$NETWORK_BIN" osd "$network_mode") || reading=N/A
            send_osd "$reading"
            wait_tick
        done ;;

    --uptime)
        while true; do
            if { read -r up_time _ < /proc/uptime; } 2>/dev/null &&
               [[ "$up_time" =~ ^[0-9]+\.[0-9]+$ ]]; then
                up_seconds="${up_time%%.*}"
                format_time reading "$((10#$up_seconds))"
                send_osd "$reading"
            else
                send_osd 'Up: N/A'
            fi
            wait_tick
        done ;;

    --gpu-power)
        if [[ "$GPU_VENDOR" == intel ]]; then gpu_power_once initial; wait_tick; fi
        while true; do
            gpu_power_once reading
            send_osd "$reading"
            wait_tick
        done ;;

    --gpu-usage)
        if [[ "$GPU_VENDOR" == intel ]]; then gpu_usage_once initial; wait_tick; fi
        while true; do
            gpu_usage_once reading
            send_osd "$reading"
            wait_tick
        done ;;

    --gpu-mem)
        while true; do
            gpu_mem_once reading
            send_osd "$reading"
            wait_tick
        done ;;

    --hud)
        cpu_usage_once initial
        cpu_power_once initial
        if [[ "$GPU_VENDOR" == intel ]]; then
            gpu_power_once initial
            gpu_usage_once initial
        fi
        wait_tick
        while true; do
            cpu_usage_once cpu_usage
            cpu_power_once cpu_watts
            cpu_temp_once cpu_temp
            ram_once ram_used_mb
            if [[ "$ram_used_mb" =~ ^[0-9]+$ ]]; then
                ram_str="$((ram_used_mb / 1024)).$(((ram_used_mb % 1024) * 10 / 1024))GB"
            else
                ram_str="N/A"
            fi
            gpu_usage=N/A gpu_watts=N/A gpu_mem=N/A gpu_temp=N/A
            vram_label=VRAM
            if [[ -n "$GPU_CARD" ]]; then
                gpu_power_once gpu_watts
                gpu_usage_once gpu_usage
                gpu_mem_once gpu_mem
                gpu_temp_once gpu_temp
            fi
            if [[ "$gpu_mem" =~ ^([0-9]+)MB$ ]]; then
                vram_mb="${BASH_REMATCH[1]}"
                gpu_vram="$((vram_mb / 1024)).$(((vram_mb % 1024) * 10 / 1024))GB"
            else
                gpu_vram="$gpu_mem"
            fi
            printf -v hud_text '  %s • %s • %s\n  %s • %s • %s\n  %s | %s %s' \
                "$cpu_usage" "$cpu_watts" "$cpu_temp" \
                "$gpu_usage" "$gpu_watts" "$gpu_temp" \
                "$ram_str" "$vram_label" "$gpu_vram"
            send_hud_osd "$hud_text"
            wait_tick
        done ;;

    --workspace)
        TICK_PERIOD_US=2000000
        while true; do
            ws_id='?'
            if ws_json=$(timeout --kill-after=1s 3s hyprctl -j activeworkspace 2>/dev/null) &&
               [[ "$ws_json" =~ \"id\"[[:space:]]*:[[:space:]]*(-?[0-9]+) ]]; then
                ws_id="${BASH_REMATCH[1]}"
            fi
            send_osd "WS: $ws_id"
            wait_tick
        done ;;
esac
