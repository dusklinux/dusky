#!/usr/bin/env bash
# Terminate the focused Hyprland window's process: its descendants, plus its process group
# when it leads one that no ancestor of this script shares.
# SIGTERM -> wait up to 1s for everything to finish -> SIGKILL only what is still alive (zombies ignored).
# Always exits 0. KFP_DEBUG=1 prints why it did nothing.

set +e +u +o pipefail
trap 'exit 0' EXIT INT TERM HUP QUIT PIPE
bail() { [[ -n ${KFP_DEBUG:-} ]] && echo "kill_focused_process: $*" >&2; exit 0; }

active_json=$(timeout 1 hyprctl -j activewindow 2>/dev/null)
if command -v jq >/dev/null 2>&1; then
    pid=$(jq -r '.pid // 0' <<< "$active_json" 2>/dev/null)
elif [[ "$active_json" =~ \"pid\"[[:space:]]*:[[:space:]]*([0-9]+) ]]; then
    pid="${BASH_REMATCH[1]}"
fi
[[ ${pid:-0} =~ ^[0-9]+$ ]] && (( pid > 1 )) || bail "no usable focused pid (got '${pid}')"

# Parse /proc/$1/stat into _state _ppid _pgid. Builtin read only: no fork per call.
read_stat() {
    local raw=""
    read -r -d '' raw 2>/dev/null < "/proc/$1/stat"
    [[ -n $raw ]] || return 1
    read -r _state _ppid _pgid _ <<< "${raw##*) }"
    [[ $_ppid =~ ^[0-9]+$ && $_pgid =~ ^[0-9]+$ ]]
}
live() { read_stat "$1" && [[ $_state != [ZXx] ]]; }

# Protect this script and every ancestor (invoking shell, Hyprland, systemd, ...) and their groups.
declare -A protected=() protected_pg=([0]=1 [1]=1)
for (( cur = $$; cur > 1; )); do
    read_stat "$cur" || break
    protected[$cur]=1; protected_pg[$_pgid]=1
    (( _ppid == cur )) && break
    cur=$_ppid
done

live "$pid" || bail "pid $pid is gone or a zombie"
[[ -O "/proc/$pid" ]] || bail "pid $pid is not owned by current user"
pgid=$_pgid
[[ -z ${protected[$pid]:-} ]] || bail "focused pid $pid is an ancestor of this script"

# Snapshot targets (focused pid + all descendants) before sending anything.
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

# Signal the whole group only when the focused process leads it and nobody we must protect is in it.
group=""
(( pgid == pid )) && [[ -z ${protected_pg[$pgid]:-} ]] && group="-$pgid"

targets_live() {
    local p
    for p in "${!targets[@]}"; do live "$p" && return 0; done
    return 1
}

# Live group members that were not in the snapshot (e.g. orphaned helpers): adopt them so they get the same grace
# period and the same SIGKILL fallback. Only runs once the snapshot has exited, so the /proc walk is not on the hot path.
adopt_stragglers() {
    local d p raw found=1
    for d in /proc/[0-9]*; do
        p=${d#/proc/}; [[ -n ${targets[$p]:-} ]] && continue
        raw=""; read -r -d '' raw 2>/dev/null < "$d/stat"
        set -- ${raw##*) }
        [[ ${3:-} == "$pgid" && ${1:-Z} != [ZXx] ]] && { targets[$p]=1; found=0; }
    done
    return $found
}

send() {  # $1 = signal number; pids already covered by the group signal are not signalled twice
    local p
    [[ -n $group ]] && kill -"$1" -- "$group" 2>/dev/null
    for p in "${!targets[@]}"; do
        live "$p" || continue
        [[ -n $group && $_pgid == "$pgid" ]] && continue
        kill -"$1" "$p" 2>/dev/null
    done
}

send 15
deadline=$(( ${EPOCHREALTIME/[.,]/} + 1000000 ))
while (( ${EPOCHREALTIME/[.,]/} < deadline )); do
    targets_live || { [[ -n $group ]] && adopt_stragglers || break; }
    sleep 0.05
done

targets_live && send 9
exit 0
