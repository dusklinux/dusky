#!/bin/bash
set -o nounset
set -o pipefail

export LC_ALL=C.UTF-8

bars=18
vert=0
clean=0

usage() {
    local fd=1
    (( ${1:-0} )) && fd=2
    printf 'Usage: %s [-h|--help] [--vert] [--clean] [--bars N | --bars=N | --N]\n' "${0##*/}" >&$fd
    exit "${1:-0}"
}

validate_bars() {
    [[ $1 =~ ^[0-9]+$ ]] && (( 10#$1 >= 1 )) || {
        printf 'Invalid bar count: %s\n' "$1" >&2
        exit 1
    }
}

while [[ $# -gt 0 ]]; do
    case "$1" in
        -h|--help) usage 0 ;;
        --vert)    vert=1 ;;
        --clean)   clean=1 ;;
        --bars)
            [[ -n ${2+x} ]] || { printf 'Missing value for --bars\n' >&2; exit 1; }
            bars="$2"
            validate_bars "$bars"
            shift
            ;;
        --bars=*)
            bars="${1#--bars=}"
            validate_bars "$bars"
            ;;
        --[0-9]*)
            bars="${1#--}"
            validate_bars "$bars"
            ;;
        *)
            printf 'Unknown option: %s\n' "$1" >&2
            usage 1
            ;;
    esac
    shift
done

command -v cava >/dev/null 2>&1 || {
    printf 'cava: command not found\n' >&2
    exit 1
}

command -v awk >/dev/null 2>&1 || {
    printf 'awk: command not found\n' >&2
    exit 1
}

case ${CAVA_GLYPHS:-unicode} in
    unicode)
        c0=$'\u2581'; c1=$'\u2582'; c2=$'\u2583'; c3=$'\u2584'
        c4=$'\u2585'; c5=$'\u2586'; c6=$'\u2587'; c7=$'\u2588'
        ;;
    ascii)
        c0='.'; c1=':'; c2='-'; c3='='
        c4='+'; c5='*'; c6='#'; c7='@'
        ;;
    *)
        printf 'Invalid CAVA_GLYPHS value: %s\n' "${CAVA_GLYPHS}" >&2
        printf 'Expected: unicode or ascii\n' >&2
        exit 1
        ;;
esac

printf -v config '%s\n' \
    '[general]' \
    "bars = $bars" \
    'framerate = 60' \
    '' \
    '[output]' \
    'method = raw' \
    'raw_target = /dev/stdout' \
    'data_format = ascii' \
    'ascii_max_range = 7'

cleanup() {
    local status=$?
    trap - EXIT INT TERM HUP

    if [[ -v awk_pid ]]; then
        kill "$awk_pid" 2>/dev/null || true
        wait "$awk_pid" 2>/dev/null || true
    fi

    if [[ -v CAVA_PID ]]; then
        kill "$CAVA_PID" 2>/dev/null || true
        wait "$CAVA_PID" 2>/dev/null || true
    fi

    exit "$status"
}

trap cleanup EXIT INT TERM HUP

coproc CAVA { cava -p /dev/fd/3 3<<<"$config"; }

# Dynamically allocate a safe FD and duplicate the coproc's output into it
exec {cava_out}<&"${CAVA[0]}"

awk \
    -v vert="$vert" \
    -v clean="$clean" \
    -v c0="$c0" -v c1="$c1" -v c2="$c2" -v c3="$c3" \
    -v c4="$c4" -v c5="$c5" -v c6="$c6" -v c7="$c7" '
BEGIN {
    c[0] = c0; c[1] = c1; c[2] = c2; c[3] = c3
    c[4] = c4; c[5] = c5; c[6] = c6; c[7] = c7

    idle = 0
    blanked = 0
    threshold = 60
}
{
    n = split($0, raw, ";")
    nbars = 0
    all_zero = 1

    for (i = 1; i <= n; i++) {
        if (raw[i] == "") continue
        nbars++

        actual = raw[i] + 0
        if (actual < 0) actual = 0
        else if (actual > 7) actual = 7

        decayed = prev[nbars] - 2
        displayed = (actual > decayed) ? actual : decayed
        if (displayed < 0) displayed = 0

        prev[nbars] = displayed
        if (displayed > 0) all_zero = 0
    }

    if (clean && all_zero) idle++
    else                   idle = 0

    if (clean && idle >= threshold) {
        if (!blanked) {
            if (vert) printf "{\"text\":\"\"}\n"
            else      printf "\n"
            fflush()
            blanked = 1
        }
        next
    }

    blanked = 0
    out = ""

    if (vert) {
        for (i = 1; i <= nbars; i++) {
            if (i > 1) out = out "\\n"
            out = out c[prev[i]]
        }
        printf "{\"text\":\"%s\"}\n", out
    } else {
        for (i = 1; i <= nbars; i++)
            out = out c[prev[i]]
        printf "%s\n", out
    }

    fflush()
}' <&"${cava_out}" &

awk_pid=$!

# Close the duplicated FD in the parent shell to avoid leaks
exec {cava_out}<&-

wait "$awk_pid"
awk_status=$?

if (( awk_status != 0 )); then
    kill "$CAVA_PID" 2>/dev/null || true
fi

wait "$CAVA_PID"
cava_status=$?

trap - EXIT INT TERM HUP

(( awk_status == 0 )) || exit "$awk_status"
exit "$cava_status"
