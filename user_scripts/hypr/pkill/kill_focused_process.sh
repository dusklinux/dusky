#!/usr/bin/env bash
# Generic, blast-radius-safe focused window process terminator for Hyprland 0.57+
# Target: Arch Linux | Kernel 7.4+ | GNU Bash 5.3+ | systemd 262

shopt -s nullglob globstar

# Configure zero-fork sub-second sleep:
# 1. Primary: Arch Linux Bash loadable C builtin (/usr/lib/bash/sleep).
# 2. Fallback: Single persistent pipe FD polled via `read -rt` (0 forks per tick).
# 3. Last resort: External /usr/bin/sleep.
sleep_mode="external"
sleep_fd=""
if [[ -r /usr/lib/bash/sleep ]] && enable -f /usr/lib/bash/sleep sleep 2>/dev/null; then
    sleep_mode="builtin"
elif exec {sleep_fd}<> <(:) 2>/dev/null; then
    sleep_mode="fd"
fi

cleanup() {
    if [[ -n "$sleep_fd" ]]; then
        exec {sleep_fd}<&- 2>/dev/null || true
    fi
}
trap cleanup EXIT

nano_sleep_50ms() {
    case "$sleep_mode" in
        builtin) sleep 0.05 ;;
        fd)      read -rt 0.05 -u "$sleep_fd" || true ;;
        *)       /usr/bin/sleep 0.05 ;;
    esac
}

# Parse /proc/$1/stat safely into _state, _ppid, _pgid, and _starttime.
read_proc_stat() {
    local p="$1" raw tail_fields
    local -a fields
    { raw=$(<"/proc/$p/stat"); } 2>/dev/null || return 1
    tail_fields="${raw##*) }"
    IFS=' ' read -ra fields <<< "$tail_fields"
    _state="${fields[0]:-}"
    _ppid="${fields[1]:-}"
    _pgid="${fields[2]:-}"
    _starttime="${fields[19]:-}"
    [[ -n "$_state" && "$_ppid" =~ ^[0-9]+$ && "$_pgid" =~ ^[0-9]+$ && "$_starttime" =~ ^[0-9]+$ ]]
}

# 1. Retrieve focused window PID from Hyprland IPC JSON.
# hyprctl returns {} on empty workspaces or when a layer-shell surface has focus.
active_json=$(hyprctl -j activewindow 2>/dev/null) || exit 0
pid=$(jq -r '.pid // 0' <<< "$active_json" 2>/dev/null) || exit 0

# Validate numeric PID > 1, owned by current UID, and alive (not a zombie).
[[ "$pid" =~ ^[0-9]+$ ]] || exit 0
(( pid > 1 )) || exit 0
[[ -O "/proc/$pid" ]] || exit 0

read_proc_stat "$pid" || exit 0
[[ "$_state" != "Z" && "$_state" != "X" && "$_state" != "x" ]] || exit 0
root_pgid="$_pgid"

# 2. Build ancestor protection tables (protects this script, shell, Hyprland, uwsm, systemd).
declare -A protected_pids=()
declare -A protected_pgids=([0]=1 [1]=1)

cur="$$"
while [[ "$cur" =~ ^[0-9]+$ ]] && (( cur > 1 )); do
    protected_pids["$cur"]=1
    if read_proc_stat "$cur"; then
        protected_pgids["$_pgid"]=1
        (( _ppid == cur )) && break
        cur="$_ppid"
    else
        break
    fi
done

# Abort if the resolved PID is an ancestor of this script.
[[ -z "${protected_pids[$pid]:-}" ]] || exit 0

# 3. Collect target PIDs pinned by starttime to prevent PID-reuse collisions.
declare -A target_starttimes=()

add_target_pid() {
    local cand="$1"
    [[ "$cand" =~ ^[0-9]+$ ]] || return 0
    (( cand > 1 )) || return 0
    [[ -z "${protected_pids[$cand]:-}" ]] || return 0
    [[ -z "${target_starttimes[$cand]:-}" ]] || return 0
    [[ -O "/proc/$cand" ]] || return 0

    if read_proc_stat "$cand"; then
        if [[ "$_state" != "Z" && "$_state" != "X" && "$_state" != "x" ]]; then
            target_starttimes["$cand"]="$_starttime"
        fi
    fi
}

# Recursive descendant traversal via /proc/$p/task/*/children (O(app processes), 0 forks).
collect_descendants() {
    local parent="$1" tid_file child_list child
    add_target_pid "$parent"
    for tid_file in "/proc/$parent/task/"*/children; do
        [[ -r "$tid_file" ]] || continue
        { child_list=$(<"$tid_file"); } 2>/dev/null || continue
        for child in $child_list; do
            if [[ -z "${target_starttimes[$child]:-}" ]]; then
                collect_descendants "$child"
            fi
        done
    done
}

# Strategy A: Systemd cgroup v2 leaf app scope/service (uwsm / app2unit / Flatpak).
use_cgroup=0
if [[ -r "/proc/$pid/cgroup" ]]; then
    { cg_line=$(<"/proc/$pid/cgroup"); } 2>/dev/null || cg_line=""
    cg_path="${cg_line#0::}"
    if [[ "$cg_path" =~ ^(.*/app\.slice/app-[^/]+\.(scope|service))(/.*)?$ ]]; then
        unit_cg_dir="/sys/fs/cgroup${BASH_REMATCH[1]}"
        if [[ -d "$unit_cg_dir" ]]; then
            cg_pids=()
            for cg_procs in "$unit_cg_dir"/**/cgroup.procs; do
                [[ -r "$cg_procs" ]] || continue
                mapfile -t chunk < "$cg_procs" 2>/dev/null || chunk=()
                cg_pids+=("${chunk[@]}")
            done

            cg_safe=1
            for p in "${cg_pids[@]}"; do
                if [[ -n "${protected_pids[$p]:-}" ]]; then
                    cg_safe=0
                    break
                fi
            done

            if (( cg_safe == 1 && ${#cg_pids[@]} > 0 )); then
                use_cgroup=1
                for p in "${cg_pids[@]}"; do
                    add_target_pid "$p"
                done
            fi
        fi
    fi
fi

# Strategy B: Targeted process tree & safe PGID leader expansion (no full /proc scan).
if (( use_cgroup == 0 )); then
    collect_descendants "$pid"

    if (( root_pgid > 1 && root_pgid != pid )) && [[ -z "${protected_pgids[$root_pgid]:-}" ]]; then
        if [[ -e "/proc/$root_pgid" ]]; then
            # Walk down from the PGID leader only if it runs the same executable
            # (captures multi-process workers without touching a parent terminal/bar/launcher).
            if [[ "/proc/$root_pgid/exe" -ef "/proc/$pid/exe" ]]; then
                collect_descendants "$root_pgid"
            fi
        else
            # Orphaned PGID fallback: PGID leader already exited and reparented children.
            mapfile -t orphan_pids < <(pgrep -u "$UID" -g "$root_pgid" 2>/dev/null)
            for cand in "${orphan_pids[@]}"; do
                collect_descendants "$cand"
            done
        fi
    fi
fi

(( ${#target_starttimes[@]} > 0 )) || exit 0

# 4. Graceful Termination: signal the focused root PID first, then remaining workers.
if [[ -n "${target_starttimes[$pid]:-}" ]]; then
    kill -15 "$pid" 2>/dev/null || true
fi
for p in "${!target_starttimes[@]}"; do
    [[ "$p" == "$pid" ]] && continue
    kill -15 "$p" 2>/dev/null || true
done

# 5. Bounded Event-Poll Loop (up to 1.0s = 20 x 50ms).
# Exits immediately once all non-zombie targets terminate; captures late-forked children.
for (( i = 0; i < 20; i++ )); do
    alive=0
    for p in "${!target_starttimes[@]}"; do
        if read_proc_stat "$p" && [[ "$_starttime" == "${target_starttimes[$p]}" ]]; then
            if [[ "$_state" != "Z" && "$_state" != "X" && "$_state" != "x" ]]; then
                alive=1
                collect_descendants "$p"
            fi
        fi
    done
    (( alive == 0 )) && exit 0
    nano_sleep_50ms
done

# 6. Forceful Escalation: SIGKILL only verified live, non-zombie survivors.
for p in "${!target_starttimes[@]}"; do
    if read_proc_stat "$p" && [[ "$_starttime" == "${target_starttimes[$p]}" ]]; then
        if [[ "$_state" != "Z" && "$_state" != "X" && "$_state" != "x" ]]; then
            kill -9 "$p" 2>/dev/null || true
        fi
    fi
done
