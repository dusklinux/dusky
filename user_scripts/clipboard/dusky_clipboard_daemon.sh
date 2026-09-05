#!/usr/bin/env bash
#d: Unified Dusky Wayland Clipboard Daemon (cliphist text, cliphist image, wl-clip-persist)
set -euo pipefail

# 1. Performance: load C builtin for sleep to eliminate binary forks
if [[ -f /usr/lib/bash/sleep ]]; then
    enable -f /usr/lib/bash/sleep sleep 2>/dev/null || true
fi
export LC_ALL=C

# 2. Resolve Wayland display & socket dynamically (zero external forks)
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

# 3. Environment Loader: pure native bash path extraction
load_env() {
    local env_file="${XDG_CONFIG_HOME:-$HOME/.config}/dusky/settings/cliphist_db_env"
    if [[ -f "$env_file" ]]; then
        # shellcheck disable=SC1090
        . "$env_file"
    fi
    export CLIPHIST_DB_PATH="${CLIPHIST_DB_PATH:-${XDG_RUNTIME_DIR:-/run/user/${UID:-$(id -u)}}/cliphist.db}"
    local db_parent="${CLIPHIST_DB_PATH%/*}"
    if [[ -n "$db_parent" && ! -d "$db_parent" ]]; then
        mkdir -p "$db_parent"
    fi
}

load_env

PERSIST_PID=0
TEXT_PID=0
IMAGE_PID=0
RUNNING=1
RELOADING=0

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
    /usr/bin/wl-paste --type text --watch sh -c '[ "$CLIPBOARD_STATE" = data ] && exec cliphist store' &
    TEXT_PID=$!
    /usr/bin/wl-paste --type image --watch sh -c '[ "$CLIPBOARD_STATE" = data ] && exec cliphist store' &
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
}

cleanup_all() {
    RUNNING=0
    stop_watchers
    if [[ $PERSIST_PID -gt 0 ]] && kill -0 "$PERSIST_PID" 2>/dev/null; then
        kill -TERM "$PERSIST_PID" 2>/dev/null || true
        PERSIST_PID=0
    fi
}

on_term() {
    cleanup_all
    exit 0
}

on_hup() {
    RELOADING=1
    load_env
    stop_watchers
    sleep 0.1
    start_watchers
    RELOADING=0
}

trap on_term SIGTERM SIGINT
trap on_hup SIGHUP
trap cleanup_all EXIT

start_persist
start_watchers

# 4. Kernel-sleeping supervisor loop using bash 5+ wait -p -n
while [[ $RUNNING -eq 1 ]]; do
    FINISHED_PID=0
    wait -p FINISHED_PID -n "$PERSIST_PID" "$TEXT_PID" "$IMAGE_PID" 2>/dev/null || true
    if [[ $RUNNING -eq 0 ]]; then
        break
    fi
    if [[ $RELOADING -eq 1 ]]; then
        continue
    fi
    # If any daemon unexpectedly terminates, trigger full clean restart via systemd
    if ! kill -0 "$PERSIST_PID" 2>/dev/null || ! kill -0 "$TEXT_PID" 2>/dev/null || ! kill -0 "$IMAGE_PID" 2>/dev/null; then
        cleanup_all
        exit 1
    fi
done

exit 0
