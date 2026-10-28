#!/usr/bin/bash
# Run after the cache unmount attempt. Capture only if the mount remains.
# Does not unmount, kill processes, or change filesystems.
set -u
mountpoint -q -- /var/cache || exit 0
readonly destination=/var/lib/dusky/diagnostics/var-cache-unmount.log
mkdir -p -- "${destination%/*}" || exit 1
umask 077
exec > "$destination" 2>&1
printf 'Cache unmount failure at %s\n' "$(date --iso-8601=seconds)"
printf '\nMount state:\n'
findmnt --target /var/cache
printf '\nRemaining processes:\n'
ps -eo pid,ppid,comm
printf '\nProcesses holding this filesystem:\n'
# Btrfs mapped-file device IDs differ from stat(); fuser/lsof can miss these.
printf '\nDirect cache mappings (PID is in each /proc path):\n'
timeout 2 rg --with-filename --no-messages ' /var/cache(/|$)' /proc/[0-9]*/maps || :
timeout 3 fuser -vm /var/cache || :
timeout 3 lsof -nP +f -- /var/cache || :
printf '\nLoop backing files:\n'
losetup --list --output NAME,BACK-FILE
printf '\nMount namespaces:\n'
lsns --type mnt --output NS,NPROCS,PID,TYPE
printf '\nMount topology:\n'
cat /proc/self/mountinfo
