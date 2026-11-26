#!/usr/bin/env bash
# =============================================================================
#  trigger.sh - Dusky Kokoro TTS desktop hook (clipboard / selection -> daemon)
#  Target: Arch Linux, Wayland / Hyprland (wl-clipboard), Bash 5.3.20+
#
#  Bind this script to a Hyprland hotkey. It reads the Wayland clipboard (or
#  primary selection with --primary) and dispatches directly over the Unix
#  domain socket so systemd socket activation handles cold starts with a single
#  Python invocation on the hot path.
# =============================================================================
set -Eeuo pipefail
shopt -s inherit_errexit

# --- locations (override with environment variables) --------------------------
CONFIG_HOME="${XDG_CONFIG_HOME:-$HOME/.config}"
DUSKY_HOME="${DUSKY_HOME:-}"
if [[ -z "$DUSKY_HOME" ]]; then
    install_path="$CONFIG_HOME/dusky-kokoro/install-path"
    if [[ -r "$install_path" ]]; then
        IFS= read -r DUSKY_HOME < "$install_path" || true
    fi
    DUSKY_HOME="${DUSKY_HOME:-$HOME/contained_apps/uv/dusky_kokoro}"
fi
VENV_PY="$DUSKY_HOME/.venv/bin/python"
MAIN_PY="$DUSKY_HOME/dusky_main.py"

RUNTIME_DIR="${XDG_RUNTIME_DIR:-/run/user/$(id -u)}"
SOCKET_PATH="${DUSKY_SOCKET:-}"
CONFIG_FILE="${DUSKY_CONFIG:-$CONFIG_HOME/dusky-kokoro/config.toml}"

STANDALONE_LOG="$RUNTIME_DIR/dusky-kokoro/daemon.log"
SOCKET_UNIT="dusky_kokoro.socket"
SERVICE_UNIT="dusky_kokoro.service"
ADHOC_UNIT="dusky_kokoro_adhoc.service"
START_TIMEOUT=45          # seconds to wait for a cold daemon
REQUEST_TIMEOUT=120       # seconds to wait for the daemon's acknowledgement

# --- request state ------------------------------------------------------------
ACTION="speak"
SOURCE="clipboard"        # clipboard | primary | text | file | stdin
SOURCE_TEXT=""
SOURCE_FILE=""
MODE=""                   # "" (daemon default) | interrupt | enqueue
VOICE=""
SPEED=""
LANG_CODE=""
WAIT=0
NOTIFY=1
LEVEL="DEBUG"

declare -A KV=()

# --- helpers ------------------------------------------------------------------
have() { command -v "$1" >/dev/null 2>&1; }

die() {
    printf 'trigger.sh: %s\n' "$*" >&2
    exit 1
}

notify() {  # summary body urgency
    [[ "$NOTIFY" == 1 ]] || return 0
    have notify-send || return 0
    notify-send -a "Dusky Kokoro" -u "$3" -t 2500 \
        -h string:x-canonical-private-synchronous:dusky-kokoro "$1" "$2" >/dev/null 2>&1 || true
}

# control client: stdlib only, isolated interpreter, no site-packages (~25-30 ms)
ctl() {
    local -a base_args=(--config "$CONFIG_FILE")
    [[ -n "$SOCKET_PATH" ]] && base_args+=(--socket "$SOCKET_PATH")
    "$VENV_PY" -I -S "$MAIN_PY" "${base_args[@]}" "$@"
}

# full interpreter (numpy / onnxruntime available): diagnostics only
ctl_full() {
    local -a base_args=(--config "$CONFIG_FILE")
    [[ -n "$SOCKET_PATH" ]] && base_args+=(--socket "$SOCKET_PATH")
    "$VENV_PY" "$MAIN_PY" "${base_args[@]}" "$@"
}

parse_kv() {
    KV=()
    local line key val
    while IFS= read -r line; do
        [[ "$line" == *=* ]] || continue
        key="${line%%=*}"
        val="${line#*=}"
        KV["$key"]="$val"
    done <<< "$1"
}

systemd_user_ok() { have systemctl && systemctl --user show-environment >/dev/null 2>&1; }
socket_unit_present() { systemctl --user cat "$SOCKET_UNIT" >/dev/null 2>&1; }

require_install() {
    [[ -x "$VENV_PY" && -f "$MAIN_PY" ]] || die "Dusky Kokoro is not installed at $DUSKY_HOME (run kokoro_installer.sh)"
}

# Zero-wake liveness check: proves whether a daemon is running without touching
# the listening systemd socket (connecting to control.sock would cold-boot it).
daemon_running() {
    local lock_dir lock_file pid_file pid state line
    if [[ -n "$SOCKET_PATH" ]]; then
        lock_dir="${SOCKET_PATH%/*}"
    else
        lock_dir="$RUNTIME_DIR/dusky-kokoro"
    fi
    lock_file="$lock_dir/daemon.lock"

    if [[ -e "$lock_file" ]]; then
        if have flock; then
            if ! flock -n "$lock_file" true 2>/dev/null; then
                return 0
            fi
        else
            pid_file="$lock_dir/daemon.pid"
            if [[ -r "$pid_file" ]]; then
                IFS= read -r pid < "$pid_file" || true
                if [[ "$pid" =~ ^[0-9]+$ && -d "/proc/$pid" ]]; then
                    return 0
                fi
            fi
        fi
    fi

    if [[ -z "$SOCKET_PATH" ]] && systemd_user_ok; then
        state=$(systemctl --user is-active "$SERVICE_UNIT" "$ADHOC_UNIT" 2>/dev/null || true)
        while IFS= read -r line; do
            case "$line" in
                active|activating) return 0 ;;
            esac
        done <<< "$state"
    fi

    # If a custom socket_path is configured in config.toml and DUSKY_SOCKET is
    # unset, let the caller probe it rather than falsely assuming idle.
    if [[ -z "$SOCKET_PATH" && -r "$CONFIG_FILE" ]]; then
        if grep -Eq '^[[:space:]]*socket_path[[:space:]]*=[[:space:]]*["'\''][^"'\'']+["'\'']' "$CONFIG_FILE"; then
            return 0
        fi
    fi

    return 1
}

wait_ready() {
    local now deadline
    # shellcheck disable=SC2154
    now="$BASH_MONOSECONDS"
    deadline=$(( now + START_TIMEOUT ))
    while (( BASH_MONOSECONDS < deadline )); do
        if ctl ping --timeout 5 >/dev/null 2>&1; then
            return 0
        fi
        sleep 0.2
    done
    notify "Kokoro TTS" "Daemon did not start within $START_TIMEOUT s (see trigger.sh --logs)" critical
    die "daemon did not become ready within $START_TIMEOUT s (trigger.sh --logs)"
}

ensure_daemon() {
    if ctl ping --timeout 3 >/dev/null 2>&1; then
        return 0
    fi
    if systemd_user_ok && socket_unit_present; then
        systemctl --user reset-failed "$SERVICE_UNIT" >/dev/null 2>&1 || true
        systemctl --user start "$SOCKET_UNIT" >/dev/null 2>&1 || true
        systemctl --user start "$SERVICE_UNIT" >/dev/null 2>&1 || true
    elif systemd_user_ok && have systemd-run; then
        systemctl --user reset-failed "$ADHOC_UNIT" >/dev/null 2>&1 || true
        local -a run_args=(--config "$CONFIG_FILE")
        [[ -n "$SOCKET_PATH" ]] && run_args+=(--socket "$SOCKET_PATH")
        systemd-run --user --quiet --collect --unit "$ADHOC_UNIT" \
            --property=KillMode=mixed --property=TimeoutStopSec=10 \
            "$VENV_PY" -X utf8 -u "$MAIN_PY" "${run_args[@]}" daemon >/dev/null 2>&1 || true
    else
        mkdir -p "${STANDALONE_LOG%/*}"
        local -a standalone_args=(--config "$CONFIG_FILE")
        [[ -n "$SOCKET_PATH" ]] && standalone_args+=(--socket "$SOCKET_PATH")
        setsid -f "$VENV_PY" -X utf8 -u "$MAIN_PY" "${standalone_args[@]}" daemon >>"$STANDALONE_LOG" 2>&1 </dev/null
    fi
    wait_ready
}

read_selection() {  # clipboard | primary -> stdout (empty when there is no text)
    local sel="$1" types mime=""
    local -a flags=()
    if [[ -z "${WAYLAND_DISPLAY:-}" ]] || ! have wl-paste; then
        die "Wayland clipboard unavailable (wl-paste and WAYLAND_DISPLAY are required)"
    fi
    [[ "$sel" == primary ]] && flags+=(--primary)
    types=$(wl-paste "${flags[@]}" --list-types 2>/dev/null || true)
    [[ -n "$types" ]] || return 0

    if grep -qiFx 'text/plain;charset=utf-8' <<< "$types"; then
        mime="text/plain;charset=utf-8"
    elif grep -qiFx 'text/plain' <<< "$types"; then
        mime="text/plain"
    elif grep -qiE '^(text/|utf8_string$|string$|text$)' <<< "$types"; then
        mime="text"
    else
        return 0
    fi
    wl-paste "${flags[@]}" --no-newline --type "$mime" 2>/dev/null || true
}

show_help() {
    cat <<'HELP'
Dusky Kokoro · read aloud

  Usage  dusky-kokoro [INPUT] [OPTIONS]
  Default input: clipboard

Input
  FILE / --file PATH   Read TXT, Markdown, PDF or EPUB
  --text "..."         Read text directly
  --primary            Read selected text
  -                    Read piped input

Speech
  --queue / --interrupt  Queue text or replace current speech
  --voice SPEC           Voice name or name:weight blend
  --speed X              Generation speed, 0.5–2.0
  --lang CODE            Language override, e.g. en-us
  --wait                 Wait for completion and report errors
  --no-notify            Disable desktop notifications

Control
  --stop / --pause       Stop, or toggle pause
  --status / --ping      Show status or check the daemon
  --reload / --restart   Apply config or restart
  --unload / --kill      Release the model or stop the daemon

Inspect
  --voices              List voices
  --doctor / --synth     Check setup or test synthesis
  --logs / --debug       Follow normal or debug logs
  --loglevel LEVEL       Change runtime logging
  -h, --help             Show this help

  Example  dusky-kokoro --file book.epub
HELP
}

# --- argument parsing ---------------------------------------------------------
while (( $# > 0 )); do
    case "$1" in
        -h|--help)      show_help; exit 0 ;;
        --primary)      SOURCE="primary" ;;
        --text)         (( $# >= 2 )) || die "--text needs an argument"; SOURCE="text"; SOURCE_TEXT="$2"; shift ;;
        --file)         (( $# >= 2 )) || die "--file needs an argument"; SOURCE="file"; SOURCE_FILE="$2"; shift ;;
        -)              SOURCE="stdin" ;;
        --queue)        MODE="enqueue" ;;
        --interrupt)    MODE="interrupt" ;;
        --voice)        (( $# >= 2 )) || die "--voice needs an argument"; VOICE="$2"; shift ;;
        --speed)        (( $# >= 2 )) || die "--speed needs an argument"; SPEED="$2"; shift ;;
        --lang)         (( $# >= 2 )) || die "--lang needs an argument"; LANG_CODE="$2"; shift ;;
        --wait)         WAIT=1 ;;
        --no-notify)    NOTIFY=0 ;;
        --stop|--pause|--status|--ping|--unload|--reload|--kill|--restart|--debug|--logs|--voices|--doctor)
                        ACTION="$1" ;;
        --synth)        ACTION="--doctor-synth" ;;
        --loglevel)     (( $# >= 2 )) || die "--loglevel needs an argument"; ACTION="--loglevel"; LEVEL="$2"; shift ;;
        --*)            die "unknown option: $1 (see --help)" ;;
        *)
            if [[ -f "$1" ]]; then
                SOURCE="file"
                SOURCE_FILE="$1"
            else
                die "unexpected argument: $1 (use --text to pass text, --file for files, or see --help)"
            fi
            ;;
    esac
    shift
done

require_install

# --- actions ------------------------------------------------------------------
case "$ACTION" in
    --stop)
        if ! daemon_running; then
            echo "daemon not running - nothing to stop"
            exit 0
        fi
        if out=$(ctl stop --format kv --timeout 5 2>/dev/null); then
            parse_kv "$out"
            if [[ "${KV[was_playing]:-}" == true ]]; then
                notify "Kokoro TTS" "Stopped" low
            fi
        else
            echo "daemon not running - nothing to stop"
        fi
        exit 0 ;;
    --pause)
        if ! daemon_running; then
            notify "Kokoro TTS" "Daemon not running" low
            exit 0
        fi
        if out=$(ctl pause --format kv --timeout 5 2>/dev/null); then
            parse_kv "$out"
            case "${KV[paused]:-}" in
                true)  notify "Kokoro TTS" "Paused" low ;;
                false) notify "Kokoro TTS" "Resumed" low ;;
                *)     notify "Kokoro TTS" "Nothing is playing" low ;;
            esac
        else
            notify "Kokoro TTS" "Daemon not running" low
        fi
        exit 0 ;;
    --status)
        if daemon_running && ctl status --format pretty --timeout 5; then
            exit 0
        fi
        echo "daemon: not running (it starts on demand)"
        if systemd_user_ok && socket_unit_present; then
            sock_state=$(systemctl --user is-active "$SOCKET_UNIT" 2>/dev/null || true)
            svc_state=$(systemctl --user is-active "$SERVICE_UNIT" 2>/dev/null || true)
            printf 'systemd: %s=%s %s=%s\n' "$SOCKET_UNIT" "$sock_state" "$SERVICE_UNIT" "$svc_state"
        fi
        exit 0 ;;
    --ping)     ctl ping --format pretty --timeout 5; exit $? ;;
    --unload)
        if ! daemon_running; then
            echo "daemon not running - model already unloaded"
            exit 0
        fi
        ctl unload --format kv --timeout 10 && echo "model unloaded"
        exit $? ;;
    --reload)
        if ! daemon_running; then
            if ctl config >/dev/null; then
                echo "config valid (daemon not running; changes will apply on next start)"
                exit 0
            fi
            exit 1
        fi
        ctl reload --format pretty --timeout 10
        exit $? ;;
    --loglevel) ctl loglevel "$LEVEL" --format kv --timeout 5; exit $? ;;
    --voices)   ctl_full voices; exit $? ;;
    --doctor)   ctl_full doctor; exit $? ;;
    --doctor-synth) ctl_full doctor --synth; exit $? ;;
    --kill)
        if daemon_running; then
            ctl shutdown --timeout 5 >/dev/null 2>&1 || true
            if systemd_user_ok; then
                systemctl --user stop "$SERVICE_UNIT" "$ADHOC_UNIT" >/dev/null 2>&1 || true
            fi
            echo "daemon stopped (the socket unit stays active: the next trigger relaunches it)"
        else
            echo "daemon not running (the socket unit stays active: the next trigger relaunches it)"
        fi
        echo "to disable completely:  systemctl --user disable --now $SOCKET_UNIT"
        exit 0 ;;
    --restart)
        if systemd_user_ok && socket_unit_present; then
            systemctl --user reset-failed "$SERVICE_UNIT" >/dev/null 2>&1 || true
            systemctl --user restart "$SERVICE_UNIT" >/dev/null 2>&1 || true
        else
            if daemon_running; then
                ctl shutdown --timeout 5 >/dev/null 2>&1 || true
            fi
            if systemd_user_ok; then
                systemctl --user stop "$SERVICE_UNIT" "$ADHOC_UNIT" >/dev/null 2>&1 || true
            fi
            sleep 0.3
            ensure_daemon
        fi
        wait_ready
        echo "daemon restarted"
        exit 0 ;;
    --debug)
        if systemd_user_ok && socket_unit_present; then
            systemctl --user set-environment DUSKY_LOG_LEVEL=DEBUG
            if daemon_running; then
                ctl shutdown --timeout 5 >/dev/null 2>&1 || true
            fi
            systemctl --user stop "$SERVICE_UNIT" >/dev/null 2>&1 || true
            ensure_daemon
            echo "DEBUG logging active for new daemon starts; revert with: systemctl --user unset-environment DUSKY_LOG_LEVEL"
        else
            ensure_daemon
            ctl loglevel DEBUG --timeout 5 >/dev/null
        fi
        ;&   # fall through to --logs
    --logs)
        if systemd_user_ok; then
            exec journalctl --user -f -n 200 -u "$SERVICE_UNIT" -u "$ADHOC_UNIT"
        elif [[ -f "$STANDALONE_LOG" ]]; then
            exec tail -n 200 -f "$STANDALONE_LOG"
        else
            die "no log available (daemon never started standalone and no systemd user session)"
        fi ;;
    speak) ;;
    *) die "internal error: unknown action $ACTION" ;;
esac

# --- speak --------------------------------------------------------------------
speak_args=(speak --format kv --client trigger --timeout "$REQUEST_TIMEOUT")
[[ -n "$MODE" ]] && speak_args+=(--mode "$MODE")
[[ -n "$VOICE" ]] && speak_args+=(--voice "$VOICE")
[[ -n "$SPEED" ]] && speak_args+=(--speed "$SPEED")
[[ -n "$LANG_CODE" ]] && speak_args+=(--lang "$LANG_CODE")

if [[ "$SOURCE" == "file" ]]; then
    [[ -f "$SOURCE_FILE" ]] || die "file not found: $SOURCE_FILE"
    [[ -r "$SOURCE_FILE" ]] || die "cannot read $SOURCE_FILE (permission denied)"
    speak_args+=(--file "$SOURCE_FILE")

    if (( WAIT )); then
        if ! daemon_running && ! { systemd_user_ok && socket_unit_present; }; then
            ensure_daemon
        fi
        ctl "${speak_args[@]}" --wait
        exit $?
    fi

    rc=0
    out=$(ctl "${speak_args[@]}") || rc=$?
    if (( rc == 2 || rc == 3 )); then
        ensure_daemon
        rc=0
        out=$(ctl "${speak_args[@]}") || rc=$?
    fi

    parse_kv "$out"
    case "${KV[event]:-}" in
        accepted)
            segments="${KV[segments]:-0}"
            chars="${KV[chars]:-0}"
            base_name="${SOURCE_FILE##*/}"
            if [[ "${KV[engine_loaded]:-}" == true ]]; then
                notify "Kokoro TTS" "Reading $base_name ($segments segments, $chars chars)" low
            else
                notify "Kokoro TTS" "Loading model... reading $base_name ($segments segments)" low
            fi
            ;;
        deduplicated)
            ;;
        *)
            err="${KV[error]:-unknown error (exit code $rc)}"
            notify "Kokoro TTS error" "$err" critical
            printf 'trigger.sh: %s\n' "$err" >&2
            exit 1 ;;
    esac
    exit 0
fi

text=""
case "$SOURCE" in
    clipboard|primary) text=$(read_selection "$SOURCE") ;;
    text)              text="$SOURCE_TEXT" ;;
    stdin)             text=$(cat) ;;
esac

if [[ "$text" != *[![:space:]]* ]]; then
    notify "Kokoro TTS" "Nothing to read: the $SOURCE contains no text" low
    exit 0
fi

speak_args+=(--stdin)

if (( WAIT )); then
    if ! daemon_running && ! { systemd_user_ok && socket_unit_present; }; then
        ensure_daemon
    fi
    printf '%s' "$text" | ctl "${speak_args[@]}" --wait
    exit $?
fi

rc=0
out=$(printf '%s' "$text" | ctl "${speak_args[@]}") || rc=$?
if (( rc == 2 || rc == 3 )); then
    # Socket inactive or idle-exit race: start daemon once and retry.
    ensure_daemon
    rc=0
    out=$(printf '%s' "$text" | ctl "${speak_args[@]}") || rc=$?
fi

parse_kv "$out"
case "${KV[event]:-}" in
    accepted)
        segments="${KV[segments]:-0}"
        chars="${KV[chars]:-0}"
        if [[ "${KV[engine_loaded]:-}" == true ]]; then
            notify "Kokoro TTS" "Reading $segments segment(s), $chars characters" low
        else
            notify "Kokoro TTS" "Loading model... reading $segments segment(s)" low
        fi
        ;;
    deduplicated)
        ;;
    *)
        err="${KV[error]:-unknown error (exit code $rc)}"
        notify "Kokoro TTS error" "$err" critical
        printf 'trigger.sh: %s\n' "$err" >&2
        exit 1 ;;
esac
