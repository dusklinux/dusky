#!/usr/bin/env bash
# Graceful session teardown for Hyprland (Lua), systemd 262 and Bash 5.3+.
# Request closure, allow 2.5 seconds, then force-kill surviving original targets.
# Force-killing a window terminates its owning process, including other windows.

set -euo pipefail

ACTION="${1:-poweroff}"
if (( $# > 1 )); then
  printf 'Usage: %s [poweroff|reboot|soft-reboot|logout]\n' "${0##*/}" >&2
  exit 1
fi
case "$ACTION" in
  poweroff|reboot|soft-reboot|logout) ;;
  *)
    printf "Error: Invalid action '%s'.\n" "$ACTION" >&2
    printf 'Usage: %s [poweroff|reboot|soft-reboot|logout]\n' "${0##*/}" >&2
    exit 1
    ;;
esac

commands=(hyprctl jq ps sleep)
[[ "$ACTION" == logout ]] || commands+=(systemctl)
for cmd in "${commands[@]}"; do
  if ! command -v "$cmd" >/dev/null 2>&1; then
    printf "Error: Required command '%s' not found in PATH.\n" "$cmd" >&2
    exit 1
  fi
done

if [[ -z "${HYPRLAND_INSTANCE_SIGNATURE:-}" ]]; then
  printf 'Warning: No Hyprland instance signature; relying on hyprctl instance discovery.\n' >&2
fi

# Do not erase persistent restart/reboot reminders before an action succeeds.
# Workspace focus is cosmetic and does not persist across compositor restarts.
hyprctl dispatch 'hl.dsp.focus({ workspace = "1" })' >/dev/null 2>&1 || :

# Protect the caller's ancestry, including its terminal's owning process.
declare -A skip_pids=()
read_ancestry() {
  local pid=$$ ppid
  while (( pid > 1 )); do
    skip_pids["$pid"]=1
    ppid=$(ps -q "$pid" -o ppid= 2>/dev/null) || return 1
    [[ "$ppid" =~ ^[[:space:]]*([0-9]+)[[:space:]]*$ ]] || return 1
    pid="${BASH_REMATCH[1]}"
    [[ -n "${skip_pids[$pid]:-}" ]] && break
  done
  return 0
}

# Capture parser failures synchronously; process substitution hides exit status.
read_clients() {
  client_rows=$(hyprctl -j clients | jq -rs '
    if length != 1 or (.[0] | type) != "array"
    then error("expected one client array")
    else .[0][] | [.pid, .address] | @tsv end
  ')
}

declare -A targets=()
build_batch() {
  local mode="$1" c_pid addr
  batch=""
  while IFS=$'\t' read -r c_pid addr; do
    [[ "$c_pid" =~ ^[1-9][0-9]*$ && "$c_pid" != 1 ]] || continue
    [[ "$addr" =~ ^0x[0-9a-fA-F]+$ ]] || continue
    [[ -n "${skip_pids[$c_pid]:-}" ]] && continue
    if [[ "$mode" == close ]]; then
      targets[$addr]="$c_pid"
    else
      # New windows and addresses now owned by another process get no SIGKILL.
      [[ "${targets[$addr]:-}" == "$c_pid" ]] || continue
    fi
    batch+="${batch:+;}dispatch hl.dsp.window.${mode}({ window = 'address:${addr}' })"
  done <<< "$client_rows"
}

# hyprctl can exit successfully even when a dispatcher reports an error.
dispatch_checked() {
  local reply status=0
  reply=$(hyprctl "$@") || status=$?
  if (( status == 0 )) &&
      [[ "$reply" =~ ^[[:space:]]*ok([[:space:]]+ok)*[[:space:]]*$ ]]; then
    printf '%s\n' "$reply"
    return 0
  fi
  printf 'Hyprland request failed: %s\n' "${reply:-no reply}" >&2
  (( status != 0 )) || status=1
  return "$status"
}

if read_ancestry && read_clients; then
  build_batch close
  if [[ -n "$batch" ]]; then
    if dispatch_checked --batch "$batch" >/dev/null; then
      # Reuse the final poll for the kill batch; no redundant sixth query.
      for attempt in {1..5}; do
        if ! sleep 0.5; then
          printf 'Warning: Grace-period wait failed; skipping force-kill.\n' >&2
          break
        fi
        if ! read_clients; then
          printf 'Warning: Cannot query clients; skipping force-kill.\n' >&2
          break
        fi
        build_batch kill
        [[ -n "$batch" ]] || break
        if (( attempt == 5 )); then
          dispatch_checked --batch "$batch" >/dev/null ||
            printf 'Warning: Force-kill request failed.\n' >&2
        fi
      done
    else
      printf 'Warning: Close request failed; skipping force-kill.\n' >&2
    fi
  fi
else
  printf 'Warning: Cannot read ancestry or clients; skipping window teardown.\n' >&2
fi

# Teardown is best-effort; preserve the final action's output and exit status.
if [[ "$ACTION" == "logout" ]]; then
  dispatch_checked dispatch 'hl.dsp.exit()'
else
  exec systemctl --no-wall "$ACTION"
fi
