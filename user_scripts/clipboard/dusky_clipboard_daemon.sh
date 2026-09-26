#!/usr/bin/env bash
#d: Unified Dusky Wayland Clipboard Daemon (cliphist text, cliphist image, wl-clip-persist)
set -euo pipefail
umask 077

export LC_ALL=C

# Environment loader: pure native bash path extraction
load_env() {
    local env_file="${XDG_CONFIG_HOME:-$HOME/.config}/dusky/settings/cliphist_db_env"
    local line val
    # Match the menu: the file is authoritative and is data, never shell code.
    unset -v CLIPHIST_DB_PATH
    if [[ -f $env_file && ! -L $env_file && -r $env_file ]]; then
        while IFS= read -r line || [[ -n $line ]]; do
            [[ $line =~ ^[[:space:]]*(export[[:space:]]+)?CLIPHIST_DB_PATH[[:space:]]*=[[:space:]]*(.*)$ ]] || continue
            val="${BASH_REMATCH[2]}"
            if   [[ $val =~ ^\"([^\"]*)\" ]]; then val="${BASH_REMATCH[1]}"
            elif [[ $val =~ ^\'([^\']*)\' ]]; then val="${BASH_REMATCH[1]}"
            else val="${val%%[[:space:]]#*}"; val="${val%%[[:space:]]*}"
            fi
            [[ $val == /?* ]] && CLIPHIST_DB_PATH="$val"
        done < "$env_file"
    fi
    export CLIPHIST_DB_PATH="${CLIPHIST_DB_PATH:-${XDG_CACHE_HOME:-$HOME/.cache}/cliphist/db}"
    local db_parent="${CLIPHIST_DB_PATH%/*}"
    if [[ -n "$db_parent" && ! -d "$db_parent" ]]; then
        mkdir -p "$db_parent"
    fi
}

# A watcher starts this callback for each selection event. Keep the watchers
# alive across storage switches: reconnecting would import the old selection.
if [[ ${1:-} == --store ]]; then
    [[ ${CLIPBOARD_STATE:-} == data ]] || exit 0
    settings="${XDG_CONFIG_HOME:-$HOME/.config}/dusky/settings"
    mkdir -p -- "$settings"
    exec {backend_fd}<>"$settings/.clipboard_backend.lock"
    flock --shared "$backend_fd"
    load_env
    # Retain the shared lock until cliphist has consumed and committed stdin.
    exec cliphist store
elif (( $# )); then
    printf 'Unknown argument: %s\n' "$1" >&2
    exit 2
fi

# Resolve Wayland display & socket dynamically (zero external forks)
if [[ -z "${WAYLAND_DISPLAY:-}" ]]; then
    for sock in "${XDG_RUNTIME_DIR:-/run/user/${UID:-$(id -u)}}"/wayland-*; do
        if [[ -S "$sock" ]]; then
            export WAYLAND_DISPLAY="${sock##*/}"
            break
        fi
    done
fi

if [[ -z "${WAYLAND_DISPLAY:-}" ]]; then
    echo "[ERROR] WAYLAND_DISPLAY is not set and no Wayland socket found in XDG_RUNTIME_DIR." >&2
    exit 1
fi


load_env

SELF=$(realpath -e -- "${BASH_SOURCE[0]}")
readonly SELF

PERSIST_PID=0
TEXT_PID=0
IMAGE_PID=0
RUNNING=1
RELOAD_REQUESTED=0

start_persist() {
    /usr/bin/wl-clip-persist \
        --clipboard regular \
        --write-timeout 8000 \
        --selection-size-limit 104857600 \
        --reconnect-tries 0 \
        --reconnect-delay 100 &
    PERSIST_PID=$!
}

start_watchers() {
    /usr/bin/wl-paste --type text --watch "$SELF" --store &
    TEXT_PID=$!
    /usr/bin/wl-paste --type image --watch "$SELF" --store &
    IMAGE_PID=$!
}

stop_watchers() {
    local old_t=$TEXT_PID old_i=$IMAGE_PID
    TEXT_PID=0
    IMAGE_PID=0
    if [[ $old_t -gt 0 ]] && kill -0 "$old_t" 2>/dev/null; then
        kill -TERM "$old_t" 2>/dev/null || true
    fi
    if [[ $old_i -gt 0 ]] && kill -0 "$old_i" 2>/dev/null; then
        kill -TERM "$old_i" 2>/dev/null || true
    fi
    # Reap before replacing the watchers. HUP only sets a flag now, so it can
    # interrupt wait without recursively launching another pair of children.
    local pid
    for pid in "$old_t" "$old_i"; do
        (( pid > 0 )) || continue
        while kill -0 "$pid" 2>/dev/null; do
            wait "$pid" 2>/dev/null || :
        done
    done
}

cleanup_all() {
    RUNNING=0
    stop_watchers
    if [[ $PERSIST_PID -gt 0 ]] && kill -0 "$PERSIST_PID" 2>/dev/null; then
        kill -TERM "$PERSIST_PID" 2>/dev/null || true
        PERSIST_PID=0
    fi
}

# Invoked by the signal trap below.
# shellcheck disable=SC2329
on_term() {
    cleanup_all
    exit 0
}

# Invoked by the signal trap below.
# shellcheck disable=SC2329
on_hup() {
    # Callbacks read configuration for every event. HUP refreshes only the
    # supervisor's environment; it must not reconnect clipboard watchers.
    RELOAD_REQUESTED=1
}

trap on_term SIGTERM SIGINT
trap on_hup SIGHUP
trap cleanup_all EXIT

start_persist
start_watchers

# Kernel-sleeping supervisor loop using bash 5+ wait -p -n
while [[ $RUNNING -eq 1 ]]; do
    if (( RELOAD_REQUESTED )); then
        RELOAD_REQUESTED=0
        load_env
        printf '[INFO] Clipboard configuration reloaded; existing watchers retained\n' >&2
        continue
    fi

    FINISHED_PID=0
    child_status=0
    wait -p FINISHED_PID -n "$PERSIST_PID" "$TEXT_PID" "$IMAGE_PID" 2>/dev/null || child_status=$?
    if [[ $RUNNING -eq 0 ]]; then
        break
    fi
    if (( RELOAD_REQUESTED )); then
        continue
    fi
    # If any daemon unexpectedly terminates, trigger full clean restart via systemd
    if ! kill -0 "$PERSIST_PID" 2>/dev/null || ! kill -0 "$TEXT_PID" 2>/dev/null || ! kill -0 "$IMAGE_PID" 2>/dev/null; then
        printf '[ERROR] Clipboard child exited: pid=%s status=%s (persist=%s text=%s image=%s); requesting service restart\n' \
            "${FINISHED_PID:-unknown}" "$child_status" "$PERSIST_PID" "$TEXT_PID" "$IMAGE_PID" >&2
        cleanup_all
        exit 1
    fi
done

exit 0
