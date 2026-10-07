#!/usr/bin/env bash
# Blocking Lazy synchronization, including plugin task failure detection.
set -euo pipefail
main() {
    local cmd config root_name
    export XDG_CONFIG_HOME="${XDG_CONFIG_HOME:-$HOME/.config}"
    export XDG_DATA_HOME="${XDG_DATA_HOME:-$HOME/.local/share}"
    export XDG_STATE_HOME="${XDG_STATE_HOME:-$HOME/.local/state}"
    export XDG_CACHE_HOME="${XDG_CACHE_HOME:-$HOME/.cache}"
    for root_name in XDG_CONFIG_HOME XDG_DATA_HOME XDG_STATE_HOME XDG_CACHE_HOME; do
        [[ "${!root_name}" == /* ]] || { printf '%s must be absolute\n' "$root_name" >&2; return 1; }
    done
    for cmd in nvim git timeout flock mkdir; do
        command -v "$cmd" >/dev/null || { printf 'Missing dependency: %s\n' "$cmd" >&2; return 1; }
    done
    if [[ "${DUSKY_NVIM_LOCK_HELD:-0}" != 1 ]]; then
        mkdir -p -- "${XDG_STATE_HOME}/dusky" || return 1
        exec 9>"${XDG_STATE_HOME}/dusky/nvim-manager.lock" || return 1
        flock --nonblocking 9 || { printf 'Another Neovim operation is running\n' >&2; return 1; }
    fi
    config="${XDG_CONFIG_HOME}/nvim"
    [[ -f "$config/init.lua" ]] || { printf 'Missing configuration: %s/init.lua\n' "$config" >&2; return 1; }
    # Explicit app name keeps synchronization aligned with the manager's paths.
    export NVIM_APPNAME=nvim
    DUSKY_NVIM_SYNC="$(dirname -- "${BASH_SOURCE[0]}")/sync.lua"
    export DUSKY_NVIM_SYNC
    timeout --kill-after=10s 15m nvim --headless -c 'lua dofile(vim.env.DUSKY_NVIM_SYNC)'
}
if [[ "${BASH_SOURCE[0]}" == "$0" ]]; then
    main "$@"
fi
