#!/usr/bin/env bash
# ==============================================================================
# multi_monitor_workspace.sh  —  Context-Aware / Banked Workspace Dispatcher
# Compatible with Hyprland 0.55+ (Lua config)
#
# Concept: monitors are sorted left-to-right by X position and assigned a
# "bank" of 10 workspaces each.
#   Monitor 0 (leftmost)  → workspaces  1–10
#   Monitor 1             → workspaces 11–20
#   Monitor 2             → workspaces 21–30
#   …and so on.
#
# Pressing SUPER+1 always means "workspace 1 for this monitor" regardless of
# which physical screen is focused.
#
# Usage:
#   multi_monitor_workspace.sh workspace            <1-10>
#   multi_monitor_workspace.sh movetoworkspace      <1-10>
#   multi_monitor_workspace.sh movetoworkspacesilent <1-10>
#
# REQUIRES: hyprctl, jq
# ==============================================================================

set -euo pipefail

# ── Help & Usage ──────────────────────────────────────────────────────────────
if [[ "${1:-}" == "-h" || "${1:-}" == "--help" ]]; then
    printf 'Usage: %s <workspace|movetoworkspace|movetoworkspacesilent> <1-10>\n' "${0##*/}"
    exit 0
fi

if [[ $# -ne 2 ]]; then
    printf 'Usage: %s <workspace|movetoworkspace|movetoworkspacesilent> <1-10>\n' "${0##*/}" >&2
    exit 1
fi

# ── Argument Validation ───────────────────────────────────────────────────────
case "$1" in
    workspace|movetoworkspace|movetoworkspacesilent) ;;
    *)
        printf 'Error: invalid action "%s".\n' "$1" >&2
        printf 'Valid actions: workspace, movetoworkspace, movetoworkspacesilent\n' >&2
        exit 1
        ;;
esac

requested_ws="$2"
if ! [[ "$requested_ws" =~ ^([1-9]|10)$ ]]; then
    printf 'Error: workspace number must be 1–10, got: %s\n' "$requested_ws" >&2
    exit 1
fi

# ── Dependency Check ──────────────────────────────────────────────────────────
if ! command -v hyprctl >/dev/null 2>&1; then
    printf 'Error: hyprctl is required but not installed.\n' >&2
    exit 1
fi

if ! command -v jq >/dev/null 2>&1; then
    printf 'Error: jq is required but not installed.\n' >&2
    exit 1
fi

# ── Determine Focused Monitor Bank Index ──────────────────────────────────────
# Active monitors are assigned workspace banks of 10.
# If DUSKY_PRIMARY_MONITOR is set (or configured in ~/.config/hypr/primary_monitor),
# that monitor is assigned Bank 0 (workspaces 1–10), and all remaining monitors
# are sorted left-to-right as Bank 1 (11–20), Bank 2 (21–30), etc.
# Otherwise, monitors are sorted left-to-right purely by physical X position.
primary_mon="${DUSKY_PRIMARY_MONITOR:-}"
if [[ -z "$primary_mon" && -f "${XDG_CONFIG_HOME:-$HOME/.config}/hypr/primary_monitor" ]]; then
    primary_mon=$(<"${XDG_CONFIG_HOME:-$HOME/.config}/hypr/primary_monitor")
    primary_mon="${primary_mon%%[[:space:]]*}"
fi

if ! monitor_index=$(hyprctl -j monitors 2>/dev/null | jq -re --arg pri "$primary_mon" '
    map(select(.disabled != true))
    | if ($pri != "" and any(.[]; .name == $pri)) then
        ([.[] | select(.name == $pri)] + ([.[] | select(.name != $pri)] | sort_by(.x, .y)))
      else
        sort_by(.x, .y)
      end
    | if length == 0 then empty else (map(.focused) | index(true) // 0) end
' 2>/dev/null); then
    if ! hyprctl instances >/dev/null 2>&1; then
        printf 'Error: Hyprland is not running or unreachable.\n' >&2
    else
        printf 'Error: could not determine focused monitor index.\n' >&2
    fi
    exit 1
fi

target_ws=$(( monitor_index * 10 + requested_ws ))

# ── Dispatch ──────────────────────────────────────────────────────────────────
# hyprctl dispatch in Hyprland 0.55+ (Lua config) requires a Lua expression.
# The quiet flag (-q) suppresses standard output ('ok') from compositor logs.
# exec replaces the shell process directly to minimize resource overhead.
case "$1" in
    workspace)
        exec hyprctl -q dispatch "hl.dsp.focus({ workspace = \"${target_ws}\" })"
        ;;
    movetoworkspace)
        exec hyprctl -q dispatch "hl.dsp.window.move({ workspace = \"${target_ws}\" })"
        ;;
    movetoworkspacesilent)
        exec hyprctl -q dispatch "hl.dsp.window.move({ workspace = \"${target_ws}\", follow = false })"
        ;;
esac
