#!/usr/bin/env bash
# Terminate the focused Hyprland window's process: its descendants, plus its process group
# when it leads one that no ancestor of this script shares.
# SIGTERM -> wait up to 1s for everything to finish -> SIGKILL only what is still alive (zombies ignored).
# Always exits 0. KFP_DEBUG=1 prints why it did nothing.

set +e +u +o pipefail
trap 'exit 0' EXIT INT TERM HUP QUIT PIPE
bail() { [[ -n ${KFP_DEBUG:-} ]] && echo "kill_focused_process: $*" >&2; exit 0; }

# 1. Resolve focused window PID with 1s IPC timeout and native Bash regex fallback if jq is absent.
active_json=$(timeout 1 hyprctl -j activewindow 2>/dev/null)
if command -v jq >/dev/null 2>&1; then
    pid=$(jq -r '.pid // 0' <<< "$active_json" 2>/dev/null)
elif [[ "$active_json" =~ \"pid\"[[:space:]]*:[[:space:]]*([0-9]+) ]]; then
    pid="${BASH_REMATCH[1]}"
fi
[[ ${pid:-0} =~ ^[0-9]+$ ]] && (( pid > 1 )) || bail "no usable focused pid (got '${pid}')"

# 2. Parse /proc/$1/stat into _state _ppid _pgid using builtin NUL-delimited read (zero forks, newline-safe).
read_stat() {
    local raw=""
    read -r -d '' raw 2>/dev/null < "/proc/$1/stat"
    [[ -n $raw ]] || return 1
    read -r _state _ppid _pgid _ <<< "${raw##*) }"
    [[ $_ppid =~ ^[0-9]+$ && $_pgid =~ ^[0-9]+$ ]]
}
live() { read_stat "$1" && [[ $_state != [ZXx] ]]; }

# 3. Protect this script and every ancestor (invoking shell, Hyprland, uwsm, systemd, ...) and their groups.
declare -A protected=() protected_pg=([0]=1 [1]=1)
for (( cur = $$; cur > 1; )); do
    read_stat "$cur" || break
    protected[$cur]=1; protected_pg[$_pgid]=1
    (( _ppid == cur )) && break
    cur=$_ppid
done

# 4. Validate target liveness, kernel signal permission (handles PR_SET_DUMPABLE=0 apps), and ancestry safety.
live "$pid" || bail "pid $pid is gone or a zombie"
kill -0 "$pid" 2>/dev/null || bail "pid $pid is not signallable by current user"
pgid=$_pgid
[[ -z ${protected[$pid]:-} ]] || bail "focused pid $pid is an ancestor of this script"

# 5. Snapshot target tree (focused pid + all descendants across threads) before sending any signals.
declare -A targets=([$pid]=1)
collect() {
    local f kid kids
    for f in /proc/$1/task/*/children; do
        kids=""; read -r kids 2>/dev/null < "$f"
        for kid in $kids; do
            [[ -n ${targets[$kid]:-} || -n ${protected[$kid]:-} ]] && continue
            targets[$kid]=1
            collect "$kid"
        done
    done
}
collect "$pid"

# 6. Signal the whole process group only when the focused process leads it and no protected ancestor shares it.
group=""
(( pgid == pid )) && [[ -z ${protected_pg[$pgid]:-} ]] && group="-$pgid"

targets_live() {
    local p
    for p in "${!targets[@]}"; do live "$p" && return 0; done
    return 1
}

# 7. Adopt live group members not in the initial snapshot (reparented helpers or late-forked stragglers)
# once the initial snapshot exits, dispatching SIGTERM to newly adopted PIDs for graceful drain.
adopt_stragglers() {
    local d p found=1
    for d in /proc/[0-9]*; do
        p=${d#/proc/}
        [[ -n ${targets[$p]:-} || -n ${protected[$p]:-} ]] && continue
        live "$p" && [[ $_pgid == "$pgid" ]] || continue
        targets[$p]=1
        kill -15 "$p" 2>/dev/null
        found=0
    done
    return $found
}

# 8. Dispatch signal to group (if eligible) and individually to any out-of-group descendants in snapshot.
send() {
    local p
    [[ -n $group ]] && kill -"$1" -- "$group" 2>/dev/null
    for p in "${!targets[@]}"; do
        live "$p" || continue
        [[ -n $group && $_pgid == "$pgid" ]] && continue
        kill -"$1" "$p" 2>/dev/null
    done
}

# 9. Graceful SIGTERM -> up to 1.0s microsecond-deadline drain -> SIGKILL only remaining live processes.
send 15
deadline=$(( ${EPOCHREALTIME/[.,]/} + 1000000 ))
while (( ${EPOCHREALTIME/[.,]/} < deadline )); do
    targets_live || { [[ -n $group ]] && adopt_stragglers || break; }
    sleep 0.05
done

targets_live && send 9
exit 0
