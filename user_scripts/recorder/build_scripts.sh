#!/usr/bin/env bash
# =============================================================================
# build_scripts.sh — GPU Screen Recorder toggle script builder
# =============================================================================

set -euo pipefail

CFG="$HOME/.config/gsr-tui/config.conf"
DIR="$HOME/user_scripts/recorder"

[[ -f "$CFG" ]] && source "$CFG" || true

# Normalise Python bools -> yes/no, treating "none"/"None" as unset
_yn() {
    local v="${!1:-}"
    case "$v" in
        True|true|1)       echo yes ;;
        False|false|0)     echo no  ;;
        none|None|"")      echo ""  ;;
        *)                 echo "$v" ;;
    esac
}
# Collapse "none"/"None"/empty -> ""
_opt() {
    local v="${!1:-}"
    case "$v" in none|None|"") echo "" ;; *) echo "$v" ;; esac
}

fallback_cpu=$(   _yn fallback_cpu)
low_power=$(      _yn low_power)
cursor=$(         _yn cursor)
dark_frame=$(     _yn dark_frame)
first_frame_ts=$( _yn first_frame_ts)
gl_debug=$(       _yn gl_debug)
verbose=$(        _yn verbose)
restore_portal=$( _yn restore_portal)

# Required — always present, with safe defaults
window="${window:-screen}"
container="${container:-mp4}"
output_dir="${output_dir:-$HOME/Videos}"
fps="${fps:-60}"
codec="${codec:-h264}"
quality="${quality:-high}"
encoder="${encoder:-gpu}"

# Optional — collapsed to "" if none/None/empty
frame_mode=$(   _opt frame_mode)
bitrate_mode=$( _opt bitrate_mode)
color_range=$(  _opt color_range)
tune=$(         _opt tune)
size="${size:-}"
region="${region:-}"
keyint="${keyint:-0}"
ffmpeg_opts="${ffmpeg_opts:-}"
plugin_path="${plugin_path:-}"
script_path="${script_path:-}"
# audio: "none"/"None" -> skip flag; "auto" -> detect sink; anything else -> use verbatim
_raw_audio="${audio:-auto}"
audio_codec="${audio_codec:-aac}"
audio_bitrate="${audio_bitrate:-}"
portal_token="${portal_token:-}"

case "$_raw_audio" in
    none|None)
        audio=""
        ;;
    auto)
        _sink=$(pactl get-default-sink 2>/dev/null || true)
        [[ -n "$_sink" ]] && audio="${_sink}.monitor" || audio=""
        ;;
    *)
        audio="$_raw_audio"
        ;;
esac

# Build base args — only required flags
args=(gpu-screen-recorder
    -w  "$window"
    -c  "$container"
    -f  "$fps"
    -k  "$codec"
    -q  "$quality"
    -encoder "$encoder"
)

# Optional — only appended when non-empty
[[ -n "$frame_mode"     ]] && args+=(-fm "$frame_mode")
[[ -n "$bitrate_mode"   ]] && args+=(-bm "$bitrate_mode")
[[ -n "$color_range"    ]] && args+=(-cr "$color_range")
[[ -n "$tune"           ]] && args+=(-tune "$tune")
[[ -n "$fallback_cpu"   ]] && args+=(-fallback-cpu-encoding "$fallback_cpu")
[[ -n "$low_power"      ]] && args+=(-low-power "$low_power")
[[ -n "$cursor"         ]] && args+=(-cursor "$cursor")
[[ -n "$dark_frame"     ]] && args+=(-df "$dark_frame")
[[ -n "$first_frame_ts" ]] && args+=(-write-first-frame-ts "$first_frame_ts")
[[ -n "$gl_debug"       ]] && args+=(-gl-debug "$gl_debug")
[[ -n "$verbose"        ]] && args+=(-v "$verbose")
[[ -n "$size"           ]] && args+=(-s "$size")
[[ -n "$region"         ]] && args+=(-region "$region")
[[ -n "$audio"          ]] && args+=(-a "$audio" -ac "$audio_codec")
[[ -n "$audio_bitrate"  ]] && args+=(-ab "$audio_bitrate")
[[ "$keyint" != "0"     ]] && args+=(-keyint "$keyint")
[[ -n "$ffmpeg_opts"    ]] && args+=(-ffmpeg-opts "$ffmpeg_opts")
[[ -n "$plugin_path"    ]] && args+=(-p "$plugin_path")
[[ -n "$script_path"    ]] && args+=(-sc "$script_path")
[[ "$restore_portal" == "yes" ]] && args+=(-restore-portal-session yes)
[[ -n "$portal_token"   ]] && args+=(-portal-session-token-filepath "$portal_token")

BASE="${args[*]}"

# Bake current window/region values into the toggle script for runtime checks
BAKED_WINDOW="$window"
BAKED_REGION="$region"

# =============================================================================
# Write toggle_record.sh
# =============================================================================
cat > "$DIR/toggle_record.sh" << SCRIPT
#!/usr/bin/env bash

# Region sanity check — must have a value when source=region
if [[ "${BAKED_WINDOW}" == "region" && -z "${BAKED_REGION}" ]]; then
    notify-send --urgency=critical 'GSR' 'Source is "region" but no region is defined.\nSet a region (e.g. 1280x720+0+0) in the TUI and rebuild.'
    exit 1
fi

LOGFILE="\$(mktemp /tmp/gsr-XXXXXX.log)"

if pidof gpu-screen-recorder > /dev/null; then
    killall gpu-screen-recorder
    notify-send -i media-playback-stop 'GSR' '⏹ Recording stopped' 2>/dev/null || true
else
    OUTFILE="${output_dir}/Video_\$(date +%Y-%m-%d_%H-%M-%S).${container}"
    ${BASE} -o "\$OUTFILE" > "\$LOGFILE" 2>&1 &
    GSR_PID=\$!
    sleep 1
    if ! kill -0 "\$GSR_PID" 2>/dev/null; then
        # Process already died — grab last meaningful error line
        ERR=\$(grep -i 'error\|failed\|cannot\|could not' "\$LOGFILE" | tail -1)
        [[ -z "\$ERR" ]] && ERR=\$(tail -1 "\$LOGFILE")
        notify-send --urgency=critical 'GSR Error' "\${ERR:-gsr exited immediately, check \$LOGFILE}"
    else
        notify-send -i media-record 'GSR' '▶ Recording started' 2>/dev/null || true
    fi
fi
SCRIPT
chmod +x "$DIR/toggle_record.sh"

echo "Written: $DIR/toggle_record.sh"
echo ""
echo "Preview:"
echo "  $BASE -o ${output_dir}/Video_\$(date +%Y-%m-%d_%H-%M-%S).${container}"
