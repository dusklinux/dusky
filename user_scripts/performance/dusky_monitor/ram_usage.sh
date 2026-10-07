#!/usr/bin/env bash
# RAM audit for Linux 7.3+, systemd 262+, Hyprland 0.56.2+, Bash 5.3+.
# Read-only except report creation and the explicit --probe cache-drop operation.
# Memory counters are overlapping views, not a universally additive ledger.
# proc/sysfs snapshots and process scans are sequential, never globally atomic.
# References: docs.kernel.org/filesystems/proc.html,
# admin-guide/mm/{ksm,transhuge}.html, admin-guide/blockdev/zram.html,
# admin-guide/cgroup-v2.html, gpu/drm-usage-stats.html.
# shellcheck disable=SC2016 # Markdown backticks in literal text are intentional.

set -euo pipefail
export LC_ALL=C
shopt -s nullglob

# ── 1. PRIVILEGE ESCALATION & ENVIRONMENT ───────────────────────────────────
PROBE=false
for arg in "$@"; do
    case "$arg" in
        --probe) PROBE=true ;;
        -h|--help)
            echo "Usage: sudo $0 [--probe] [-h|--help]"
            echo "  --probe    Run empirical reclaimability self-test (sync + drop_caches)"
            echo "  -h, --help Show this help message"
            exit 0
            ;;
        *) printf 'Unknown argument: %s\n' "$arg" >&2; exit 2 ;;
    esac
done

if [[ "$EUID" -ne 0 ]]; then
    echo -e "\e[1;33m[!] Elevated privileges required. Auto-elevating...\e[0m"
    exec sudo ORIGINAL_USER="$(id -un)" bash "$0" "$@"
fi

TARGET_USER="${ORIGINAL_USER:-${SUDO_USER:-$(id -un)}}"
PASSWD_ENTRY=$(getent passwd "$TARGET_USER")
IFS=: read -r _ _ TARGET_UID TARGET_GID _ TARGET_HOME _ <<< "$PASSWD_ENTRY"
[[ -n "$TARGET_HOME" && -d "$TARGET_HOME" ]] || { echo "User home unavailable" >&2; exit 1; }
REPORT_DIR="$TARGET_HOME/Documents/logs/ram_audit"
sudo -u "#$TARGET_UID" mkdir -p -- "$REPORT_DIR"
# Only change ownership of this audit's directories, never unrelated logs.
chown "$TARGET_UID:$TARGET_GID" "$REPORT_DIR"
REPORT=$(mktemp "$REPORT_DIR/report_$(date +%Y%m%d_%H%M%S)_XXXXXX.md")
WORK_DIR=$(mktemp -d)
cleanup() { rm -rf -- "$WORK_DIR"; }
trap cleanup EXIT
trap 'exit 130' INT
trap 'exit 143' TERM
trap 'printf "Audit failed; partial report: %s\n" "$REPORT" >&2' ERR
chown "$TARGET_UID:$TARGET_GID" "$REPORT"

# Optional tools never trigger package installation. Their sections report failure.
for tool in awk python grep sort head tee; do
    command -v "$tool" >/dev/null || { echo "Required command missing: $tool" >&2; exit 1; }
done
PAGE_KB=$(( $(getconf PAGESIZE) / 1024 ))

# ── 3. HELPERS ───────────────────────────────────────────────────────────────

# Read meminfo and vmstat once; reuse the exact values throughout the report.
SNAPSHOT_TIME=$(date --iso-8601=seconds)
MEMINFO=$(</proc/meminfo)
VMSTAT=$(</proc/vmstat)
declare -A MEM VM
while read -r key value _; do MEM[${key%:}]=$value; done <<< "$MEMINFO"
while read -r key value _; do VM[$key]=$value; done <<< "$VMSTAT"
for field in MemTotal MemFree MemAvailable Cached Shmem SwapTotal SwapFree; do
    [[ ${MEM[$field]:-} =~ ^[0-9]+$ ]] || { echo "Missing/invalid required meminfo field: $field" >&2; exit 1; }
done

to_mib() {
    local val="${1:-0}"
    if [[ $val == N/A ]]; then printf 'N/A'; return; fi
    awk -v val="$val" 'BEGIN {printf "%.1f", val / 1024}'
}

mem_mib() { to_mib "${MEM[$1]:-N/A}"; }

# One smaps_rollup read per process. Holding the proc directory prevents mixing
# a recycled PID's name and mappings. Exits/access failures are counted explicitly.
python - "$WORK_DIR" <<'PROCESSES'
import collections
import errno
import json
import os
from pathlib import Path
import sys
import time

out = Path(sys.argv[1])
rows = []
failures = collections.Counter()
drm = {}
drm_errors = 0
started = time.monotonic()
for path in Path('/proc').iterdir():
    if not path.name.isdecimal():
        continue
    try:
        directory = os.open(path, os.O_RDONLY | os.O_DIRECTORY)
    except OSError as exc:
        failures['exited' if exc.errno in (errno.ENOENT, errno.ESRCH) else 'inaccessible'] += 1
        continue
    def read(name):
        with os.fdopen(os.open(name, os.O_RDONLY, dir_fd=directory), encoding='utf-8', errors='replace') as stream:
            return stream.read()
    try:
        status = dict(line.split(':', 1) for line in read('status').splitlines() if ':' in line)
        if status.get('Kthread', '').strip() == '1':
            failures['kernel threads'] += 1
            continue
        metrics = {}
        for line in read('smaps_rollup').splitlines():
            key, _, value = line.partition(':')
            if value.strip().split() and value.strip().split()[0].isdigit():
                metrics[key] = int(value.split()[0])
        if 'Pss' not in metrics:
            failures['no mappings'] += 1
            continue
        comm=read('comm').strip()
        try:
            executable=Path(os.readlink('exe', dir_fd=directory)).name
        except OSError:
            executable=comm
        rows.append(dict(pid=int(path.name), comm=comm, executable=executable,
                         VmLck=int(status.get('VmLck','0').split()[0]),
                         VmPin=int(status.get('VmPin','0').split()[0]), **metrics))
        # Inspect only DRM descriptors; use directory-relative access for PID reuse.
        try:
            fd_directory=os.open('fd', os.O_RDONLY | os.O_DIRECTORY, dir_fd=directory)
            try:
                for fd in os.listdir(fd_directory):
                    try:
                        target=os.readlink(fd, dir_fd=fd_directory)
                        if not target.startswith('/dev/dri/'):
                            continue
                        info=dict(line.split(':',1) for line in read('fdinfo/'+fd).splitlines() if ':' in line)
                        info={key:value.strip() for key,value in info.items()}
                        if 'drm-driver' not in info:
                            continue
                        device=info.get('drm-pdev') or str(os.stat(fd, dir_fd=fd_directory).st_rdev)
                        client=info.get('drm-client-id')
                        identity=(info['drm-driver'],device,client) if client is not None else (path.name,fd)
                        if identity in drm:
                            drm[identity]['holders'].add((int(path.name),comm))
                        else:
                            drm[identity]=dict(info=info,holders={(int(path.name),comm)})
                    except OSError:
                        drm_errors += 1
            finally:
                os.close(fd_directory)
        except OSError:
            drm_errors += 1
    except OSError as exc:
        failures['exited/no mappings' if exc.errno in (errno.ENOENT, errno.ESRCH) else 'inaccessible'] += 1
    finally:
        os.close(directory)
rows.sort(key=lambda row: (-row['Pss'], row['pid']))
(out / 'processes.json').write_text(json.dumps(rows))
(out / 'drm.json').write_text(json.dumps([dict(info=r['info'],holders=sorted(r['holders'])) for r in drm.values()],indent=2)+'\n')
def clean(text):
    return text.replace('|', '-').replace('`', "'").replace('\t', ' ').replace('\n', ' ').replace('\r', ' ')
with (out / 'pss.md').open('w') as stream:
    stream.write('| PID | Command | USS MiB | PSS MiB | RSS MiB | PSS anon/file/shmem MiB | Swap MiB | SwapPss MiB | HugeTLB private/shared MiB |\n')
    stream.write('|---:|---|---:|---:|---:|---|---:|---:|---:|\n')
    for r in rows:  # Keep every successfully sampled process in the appendix.
        stream.write(f"| {r['pid']} | {clean(r['comm'])} | {(r.get('Private_Clean',0)+r.get('Private_Dirty',0))/1024:.1f} | {r['Pss']/1024:.1f} | {r.get('Rss',0)/1024:.1f} | {r.get('Pss_Anon',0)/1024:.1f}/{r.get('Pss_File',0)/1024:.1f}/{r.get('Pss_Shmem',0)/1024:.1f} | {r.get('Swap',0)/1024:.1f} | {r.get('SwapPss',0)/1024:.1f} | {r.get('Private_Hugetlb',0)/1024:.1f}/{r.get('Shared_Hugetlb',0)/1024:.1f} |\n")
with (out / 'locked.md').open('w') as stream:
    stream.write('| PID | Command | VmLck MiB | VmPin MiB |\n|---:|---|---:|---:|\n')
    for r in rows:
        if r['VmLck'] or r['VmPin']:
            stream.write(f"| {r['pid']} | {clean(r['comm'])} | {r['VmLck']/1024:.1f} | {r['VmPin']/1024:.1f} |\n")
with (out / 'drm.md').open('w') as stream:
    stream.write('| Holders | Driver/device/client | Memory counter | MiB |\n|---|---|---|---:|\n')
    count=0
    for record in drm.values():
        info=record['info']
        for key,value in info.items():
            if not key.startswith(('drm-total-', 'drm-shared-', 'drm-resident-', 'drm-purgeable-', 'drm-active-')):
                continue
            parts=value.split()
            if not parts or not parts[0].isdigit():
                continue
            unit=parts[1] if len(parts)>1 else 'bytes'
            if unit not in ('bytes','KiB','MiB'):
                continue
            size=int(parts[0])*{'bytes':1,'KiB':1024,'MiB':1048576}[unit]
            if size == 0:
                continue
            holders=', '.join(f'{clean(comm)}[{pid}]' for pid,comm in sorted(record['holders']))
            stream.write(f"| {holders} | {clean(info['drm-driver'])}/{info.get('drm-pdev','non-PCI')}/{info.get('drm-client-id','unknown')} | {key} | {size/1048576:.1f} |\n")
            count+=1
    stream.write('\n')
    if not count:
        stream.write('No nonzero standardized memory counters in sampled DRM descriptors; see raw appendix for zero/unsupported counters.\n')
        stream.write('\n')
    stream.write(f'FD read failures/exits: {drm_errors}. Shared/active/purgeable counters overlap totals/resident; clients can share buffers. Unknown client IDs cannot be deduplicated. Deprecated drm-memory aliases are not summed.\n')
keys = ['Pss', 'Rss', 'Swap', 'SwapPss']
(out / 'process_totals').write_text(' '.join(map(str, [len(rows), *[sum(r.get(k,0) for r in rows) for k in keys]]))+'\n')
(out / 'process_coverage').write_text(f'Scan: {time.monotonic()-started:.3f}s; sampled {len(rows)} processes; skipped {dict(failures)}. PSS/RSS exclude HugeTLB; Swap may duplicate shared swap, whereas SwapPss apportions it.\n')
if rows:
    r=rows[0]
    (out / 'largest').write_text(f"{r['pid']}\t{clean(r['comm'])}\t{r['Pss']/1024:.1f}\n")
PROCESSES
pss_table() { head -n "$(( ${1:-25} + 2 ))" "$WORK_DIR/pss.md"; }

# ── 4. FORENSICS ─────────────────────────────────────────────────────────────
echo -e "\e[1;32m[*] Commencing Deep Kernel RAM Analysis (Hyprland + Arch Linux)...\e[0m"

{
echo "# System RAM Audit — Hyprland"
echo "**Date:** $(date)"
echo "**Kernel:** $(uname -r)"
echo "**Host:** $(hostname)"
echo "**Memory snapshot:** $SNAPSHOT_TIME; base page size: $PAGE_KB KiB. Later sections are sequential samples."
echo ""

# ─────────────────────────────────────────────────────────────────────────────
# SECTION 1 — COMPLETE /proc/meminfo ACCOUNTING
# ─────────────────────────────────────────────────────────────────────────────
# Derived values reuse the single meminfo snapshot captured before the process scan.

MEM_TOTAL=${MEM[MemTotal]:-0}
MEM_FREE=${MEM[MemFree]:-0}
MEM_AVAIL=${MEM[MemAvailable]:-0}
CACHED=${MEM[Cached]:-0}
SHMEM=${MEM[Shmem]:-0}

GPU_ACTIVE=${MEM[GPUActive]:-0}
GPU_RECLAIM=${MEM[GPUReclaim]:-0}

SWAP_TOTAL=${MEM[SwapTotal]:-0}
SWAP_FREE=${MEM[SwapFree]:-0}
WRITEBACK_TMP=${MEM[WritebackTmp]:-0}
COMMITTED=${MEM[Committed_AS]:-0}
COMMIT_LIMIT=${MEM[CommitLimit]:-0}
HW_CORRUPTED=${MEM[HardwareCorrupted]:-0}

# Refined Calculations
FILE_CACHE=$(( CACHED - SHMEM ))
(( FILE_CACHE < 0 )) && FILE_CACHE=0

ZRAM_TOTAL_KB=0
ZRAM_PEAK_KB=0
ZRAM_COUNT=0
for zdev in /sys/block/zram[0-9]*; do
    [[ -r "$zdev/mm_stat" ]] || continue
    if read -r _ _ current _ peak _ < "$zdev/mm_stat"; then
        ZRAM_TOTAL_KB=$(( ZRAM_TOTAL_KB + current / 1024 ))
        ZRAM_PEAK_KB=$(( ZRAM_PEAK_KB + peak / 1024 ))
        ZRAM_COUNT=$(( ZRAM_COUNT + 1 ))
    fi
done
KERNEL_FILE_KB=$(( ${VM[nr_kernel_file_pages]:-0} * PAGE_KB ))
# Modern VmallocUsed is NR_VMALLOC physical pages, not virtual address space.
USED_KB=$(( MEM_TOTAL - MEM_AVAIL ))
ALLOCATED_KB=$(( MEM_TOTAL - MEM_FREE ))
# These kernel views can overlap (e.g. vmalloc stacks); never sum them into truth.
read -r PROC_COUNT USR_PSS_KB USR_RSS_KB USR_SWAP_KB USR_SWAP_PSS_KB < "$WORK_DIR/process_totals"

# ── 0. EXECUTIVE SUMMARY (one-glance health check) ───────────────────────────
echo ""
echo "## 0. Executive Summary"
echo "---"
echo '> *One-glance health check. Displayed memory units are MiB unless otherwise stated. See Section 1 for the full breakdown.*'
echo ""
echo "| Metric | MiB |"
echo "|---|---:|"
printf "| Total RAM (MemTotal) | %s |\n" "$(mem_mib MemTotal)"
printf "| Available (estimate) | %s |\n" "$(mem_mib MemAvailable)"
printf "| Raw Free | %s |\n" "$(mem_mib MemFree)"
printf "| Used/unavailable (MemTotal − MemAvailable) | %s |\n" "$(to_mib "$USED_KB")"
printf "| All processes combined (Σ PSS, $PROC_COUNT procs) | %s |\n" "$(to_mib "$USR_PSS_KB")"
printf "| Resident anonymous mappings (AnonPages) | %s |\n" "$(mem_mib AnonPages)"
printf "| File cache (Cached − Shmem; not all immediately reclaimable) | %s |\n" "$(to_mib "$FILE_CACHE")"
printf "| Shared memory / tmpfs (Shmem) | %s |\n" "$(mem_mib Shmem)"
printf "| Unevictable LRU (overlaps anonymous/file views) | %s |\n" "$(mem_mib Unevictable)"
printf "| Allocated RAM (MemTotal − MemFree) | %s |\n" "$(to_mib "$ALLOCATED_KB")"
printf "| ZRAM Compressed Pool | %s |\n" "$(to_mib "$ZRAM_TOTAL_KB")"
echo "> Allocation views below overlap; no fabricated residual or universal additive total is reported."
ALERTS=""

if [[ $(</proc/sys/vm/overcommit_memory) == 2 && $COMMIT_LIMIT -gt 0 ]] && (( COMMITTED * 100 / COMMIT_LIMIT > 90 )); then ALERTS+="⚠ Commit ratio >90% (Sec 2); "; fi
if (( MEM_AVAIL * 100 / (MEM_TOTAL > 0 ? MEM_TOTAL : 1) < 5 )); then ALERTS+="⚠ Critically low free RAM (<5% avail); "; fi
[[ -z "$ALERTS" ]] && ALERTS="✅ No low-availability or strict-commit threshold flags; this is not a leak verdict."
echo "> **Alerts:** $ALERTS"
TOP_PSS_LINE=""
[[ ! -f "$WORK_DIR/largest" ]] || TOP_PSS_LINE=$(<"$WORK_DIR/largest")
if [[ -n "$TOP_PSS_LINE" ]]; then
    TOP_PID=${TOP_PSS_LINE%%$'\t'*}
    rest=${TOP_PSS_LINE#*$'\t'}
    TOP_MB=${rest##*$'\t'}
    TOP_COMM=${rest%$'\t'*}
    echo "> **Largest app by PSS:** \`${TOP_COMM:-?}\` (PID ${TOP_PID:-?}) = **${TOP_MB:-0} MiB**."
else
    echo "> **Largest app by PSS:** see Section 4."
fi
echo "> **GPU buffer views:** Section 8 (logical sizes and driver-reported residency)."
echo ""
echo "### Report Index"
echo ""
echo "| # | Section |"
echo "|---|---|"
echo "| 1 | Memory counter views (overlapping) |"
echo "| 2 | Virtual Memory Commit Pressure |"
echo "| 3 | Compressed RAM (ZRAM / ZSWAP) |"
echo "| 4 | Process Memory Attribution (Top 25 by PSS; Full Process Appendix) |"
echo "| 5 | Wayland & Hyprland Diagnostics |"
echo "| 6 | Shared Memory & Tmpfs |"
echo "| 7 | Kernel Slab, KSM, Modules, vmalloc and Fragmentation |"
echo "| 8 | GPU DMA-BUF Allocations |"
echo "| 9 | Transparent Hugepages (THP) |"
echo "| 10 | Hyprland Rendering Diagnostics |"
echo "| 11 | Memory Pressure Events (OOM) |"
echo "| 11b | Reclaimability Self-Test (\`--probe\`, opt-in) |"
echo "| 12 | Quick Diagnosis Guide |"
echo "| 13 | Kernel measurement limits |"
echo ""

# ─────────────────────────────────────────────────────────────────────────────
# SECTION 1 — COMPLETE /proc/meminfo ACCOUNTING (header text)
# ─────────────────────────────────────────────────────────────────────────────
echo "## 1. Memory Counters and Accounting Boundaries"
echo "---"
echo '> **Understanding this section:** Kernel counters describe overlapping views of RAM. Only MemFree + (MemTotal − MemFree) is an exact partition here; MemAvailable estimates what new workloads could use without swapping.'
echo '> * **AnonPages:** Resident anonymous mappings; excludes swapped anonymous pages.'
echo '> * **Cached:** Files kept in RAM to make the system fast (`File Cache + Shmem`). *Clean file cache is automatically freed under memory pressure.*'
echo '> * **Shmem:** Shared Memory & tmpfs. Includes shared anonymous mappings, memfd and SysV shared memory; some Wayland buffers use this storage.'
echo ""

echo "### Overall"
echo ""
echo "| Metric | MiB |"
echo "|---|---:|"
printf "| Total Usable RAM (MemTotal) | %s |\n" "$(mem_mib MemTotal)"
printf "| Available (estimate) (MemAvailable) | %s |\n" "$(mem_mib MemAvailable)"
printf "| Raw Free (MemFree) | %s |\n" "$(mem_mib MemFree)"
printf "| Used/unavailable (modern free: total − available) | %s |\n" "$(to_mib "$USED_KB")"
echo ""
echo "> **Tool reconciliation:** modern free(1) used = MemTotal − MemAvailable. Committed_AS is a separate virtual-memory promise, not physical usage."
echo ""
echo "### Named Allocations"
echo ""
echo "| Category | MiB |"
echo "|---|---:|"
printf "| Userspace Anon (AnonPages) | %s |\n" "$(mem_mib AnonPages)"
printf "| Page Cache / File-backed (Cached − Shmem) | %s |\n" "$(to_mib "$FILE_CACHE")"
printf "| Shared Memory/Tmpfs (Shmem) | %s |\n" "$(mem_mib Shmem)"
printf "| Buffer Cache (Buffers) | %s |\n" "$(mem_mib Buffers)"
printf "| Swap cache (excluded from Cached; overlaps other views) | %s |\n" "$(mem_mib SwapCached)"
printf "| Mapped (file-mapped only on 7.x) | %s |\n" "$(mem_mib Mapped)"
printf "| Mlocked (subset of Unevictable) | %s |\n" "$(mem_mib Mlocked)"
printf "| Unevictable LRU (not additive to AnonPages/Cached) | %s |\n" "$(mem_mib Unevictable)"
[[ $GPU_ACTIVE -gt 0 || $GPU_RECLAIM -gt 0 ]] && printf "| GPU-managed active/reclaim (7.x) | %s |\n" "$(to_mib $((GPU_ACTIVE + GPU_RECLAIM)))"
echo ""
echo "### Kernel Structures"
echo ""
echo "| Category | MiB |"
echo "|---|---:|"
printf "| Slab Total | %s |\n" "$(mem_mib Slab)"
printf "| ├─ Reclaimable slab (SReclaimable) | %s |\n" "$(mem_mib SReclaimable)"
printf "| └─ Unreclaimable (SUnreclaim) | %s |\n" "$(mem_mib SUnreclaim)"
printf "| Kernel Stacks | %s |\n" "$(mem_mib KernelStack)"
printf "| Page Tables | %s |\n" "$(mem_mib PageTables)"
printf "| Secondary Page Tables (KVM/arm) | %s |\n" "$(mem_mib SecPageTables)"
printf "| Per-CPU Allocations | %s |\n" "$(mem_mib Percpu)"
printf "| Physical vmalloc pages (VmallocUsed) | %s |\n" "$(mem_mib VmallocUsed)"
printf "| KReclaimable (includes SReclaimable and other shrinkers) | %s |\n" "$(mem_mib KReclaimable)"
printf "| Kernel file pages (vmstat view; overlaps cache) | %s |\n" "$(to_mib "$KERNEL_FILE_KB")"
echo ""
echo "### Accounting boundaries"
echo '> AnonPages, Cached, Shmem, Unevictable, Mapped, THP, GPU counters, vmalloc and compression pools cannot be indiscriminately summed. Shmem is included in Cached; mlocked anonymous/file pages remain in their original counters. A near-zero arithmetic remainder does not prove attribution.'
echo '> DMA-BUF/GEM sizes may include shared objects, uninstantiated buffers and dedicated VRAM. Neither these sizes nor cgroup totals are additional global RAM buckets.'
echo '> MemTotal excludes some boot-reserved memory and kernel image allocations. No proc-based report can attribute every physical byte or prove a leak from a single snapshot.'
echo ""
echo "### Full meminfo snapshot (all fields, original units)"
echo ""
echo '```text'
printf '%s\n' "$MEMINFO"
echo '```'
echo ""

echo "### Process Lock/Pin Counters"
echo '> VmLck/VmPin are partial process views, not a census of SHM_LOCK, VFIO/GUP pins or driver buffers. Zero does not prove absence of pinned memory.'
cat "$WORK_DIR/locked.md"
echo ""

echo "### 1a. Cgroup v2 Memory Ownership and Limits"
echo '> Hierarchical totals overlap their children. Self = current − sampled immediate children, an estimate that can be negative during concurrent activity. OOM counts below are local. Root is not a complete memory.current ledger; uncharged memory cannot be attributed from this map.'
python - <<'CGROUPS'
from pathlib import Path
from collections import defaultdict
root=Path('/sys/fs/cgroup')
def read(path):
    try: return path.read_text().strip()
    except OSError: return None
def counters(path):
    return dict(line.split() for line in (read(path) or '').splitlines() if len(line.split())==2)
if not (root/'cgroup.controllers').exists():
    print('Unavailable: cgroup v2 not mounted here.')
else:
    groups={}
    for directory, _, _ in root.walk(on_error=lambda error: None):
        cur=read(directory/'memory.current')
        if cur is not None:
            groups[directory]=(int(cur), counters(directory/'memory.stat'))
    child_totals=defaultdict(int)
    for path,(current,_) in groups.items():
        child_totals[path.parent]+=current
    print('| Cgroup | Current MiB (hierarchical) | Self MiB (estimate) | Unevictable MiB (hierarchical) | Swap MiB | High/Max bytes | Local OOM kills | Local memory events |')
    print('|---|---:|---:|---:|---:|---|---:|---|')
    for path,(current,stats) in sorted(groups.items(),key=lambda item:-item[1][0]):
        swap=read(path/'memory.swap.current')
        events=counters(path/'memory.events.local')
        unevictable=stats.get('unevictable')
        unevictable_mib=f'{int(unevictable)/1048576:.1f}' if unevictable is not None else 'N/A'
        swap_mib=f'{int(swap)/1048576:.1f}' if swap is not None else 'N/A'
        name=str(path.relative_to(root)).replace('|','-').replace('\n',' ')
        print(f"| /{name} | {current/1048576:.1f} | {(current-child_totals[path])/1048576:.1f} | {unevictable_mib} | {swap_mib} | {read(path/'memory.high') or 'N/A'}/{read(path/'memory.max') or 'N/A'} | {events.get('oom_kill','N/A')} | {'; '.join(f'{key}={value}' for key,value in events.items()) or 'N/A'} |")
    if not groups: print('\nUnavailable: no readable memory controller groups.')
CGROUPS
echo ""

echo "### 1b. NUMA Node Memory (original KiB counters)"
echo ""
echo '```text'
for node in /sys/devices/system/node/node[0-9]*; do
    [[ ! -r "$node/meminfo" ]] || cat "$node/meminfo"
done
echo '```'
echo '> Per-node availability can constrain allocation even when global RAM is plentiful. Node counters overlap the global memory views; they are not extra allocations.'
echo ""

# ─────────────────────────────────────────────────────────────────────────────
# SECTION 2 — COMMIT PRESSURE & VIRTUAL OVERCOMMIT
# ─────────────────────────────────────────────────────────────────────────────
echo "## 2. Virtual Memory Commit Pressure"
echo "---"
echo "> **Understanding this section:** Virtual-memory commitments and dirty/writeback counters. Commit ratio alone cannot predict an OOM kill."
echo ""
echo "\`\`\`text"
printf "%-45s %8s MiB\n" "  CommitLimit:"   "$(mem_mib CommitLimit)"
printf "%-45s %8s MiB\n" "  Committed_AS:"  "$(mem_mib Committed_AS)"
printf "%-45s %8s MiB\n" "  Dirty pages:"   "$(mem_mib Dirty)"
printf "%-45s %8s MiB\n" "  In writeback:"  "$(mem_mib Writeback)"
[[ $WRITEBACK_TMP -gt 0 ]] && printf "%-45s %8s MiB\n" "  FUSE writeback (WritebackTmp):" "$(mem_mib WritebackTmp)"
[[ $HW_CORRUPTED -gt 0 ]] && printf "%-45s %8s MiB\n" "  *** HW CORRUPTED RAM ***:" "$(mem_mib HardwareCorrupted)"
echo "\`\`\`"
OVERCOMMIT=$(( COMMITTED * 100 / (COMMIT_LIMIT > 0 ? COMMIT_LIMIT : 1) ))
echo "- **Overcommit mode:** $(</proc/sys/vm/overcommit_memory) (0=heuristic, 1=always, 2=strict)"
echo "- **Commit ratio:** ${OVERCOMMIT}%  *(enforced only in overcommit mode 2; not a swap-pressure measurement)*"
echo ""

# ─────────────────────────────────────────────────────────────────────────────
# SECTION 3 — ZRAM & SWAP
# ─────────────────────────────────────────────────────────────────────────────
echo "## 3. Compressed RAM (ZRAM / ZSWAP)"
echo "---"
echo '> **Understanding this section:** Zram is a compressed RAM block device (swap or filesystem); zswap caches swapped pages ahead of a backing swap device. TOTAL reports zram allocator pool usage, not every driver allocation.'
echo ""
SWAP_USED=$(( SWAP_TOTAL - SWAP_FREE ))
echo "- **Swap total/free/used:** $(mem_mib SwapTotal) / $(mem_mib SwapFree) / $(to_mib "$SWAP_USED") MiB"
echo ""
echo '```text'
swapon --show=NAME,TYPE,SIZE,USED,PRIO --bytes 2>&1 || echo "Swap device listing unavailable."
echo '```'
if (( ZRAM_COUNT > 0 )); then
    echo "- **ZRAM pool current / sum of per-device peaks:** $(to_mib "$ZRAM_TOTAL_KB") / $(to_mib "$ZRAM_PEAK_KB") MiB (peaks need not coincide)."
    echo '```text'
    zramctl --output NAME,ALGORITHM,DISKSIZE,DATA,COMPR,TOTAL,MEM-USED,COMP-RATIO,MOUNTPOINT 2>&1 || echo 'zramctl unavailable; sysfs statistics below remain usable.'
    for zdev in /sys/block/zram[0-9]*; do
        echo "${zdev##*/}:"
        for metric in mm_stat io_stat backing_dev bd_stat writeback_limit_enable writeback_limit comp_algorithm recomp_algorithm; do
            [[ ! -r "$zdev/$metric" ]] || printf '  %s: %s\n' "$metric" "$(<"$zdev/$metric")"
        done
    done
    echo '```'
    echo '> mm_stat: original bytes, compressed bytes, pool bytes, limit, peak, same-filled pages, compacted pages, incompressible pages, cumulative incompressible pages. bd_stat uses fixed 4 KiB units for stored data/reads/writes; io_stat reports failed reads/writes, invalid I/O and free notifications.'
else
    echo 'No readable zram device statistics.'
fi
echo ""
if [[ -d /sys/module/zswap ]]; then
    echo "- **Zswap pool (enabled state below):** \`$(mem_mib Zswap) MiB\` physical pool, storing \`$(mem_mib Zswapped) MiB\` of decompressed data."
    echo "- **Zswap settings:**"
    echo "\`\`\`text"
    for parameter in /sys/module/zswap/parameters/*; do
        [[ ! -r "$parameter" ]] || printf '  %s: %s\n' "${parameter##*/}" "$(<"$parameter")"
    done
    echo "\`\`\`"
fi
echo ""

# ─────────────────────────────────────────────────────────────────────────────
# SECTION 4 — NATIVE PSS TABLE
# ─────────────────────────────────────────────────────────────────────────────
echo "## 4. Process Memory Attribution (Top 25 by PSS; Full Process Appendix)"
echo "---"
echo '> **Understanding this section:** Standard system monitors look at `RSS` which wildly exaggerates memory usage by double-counting shared libraries. PSS apportions shared resident mappings and is useful for comparing process footprints.'
echo '> * **USS:** Private clean + dirty mapped pages. Exit does not guarantee immediate release of that amount (file cache and kernel references may remain).'
echo '> * **PSS:** Private resident pages plus a proportional share of all shared resident mappings, not just libraries.'
echo ""
pss_table 25
cat "$WORK_DIR/process_coverage"
echo ""
echo "### All Processes Combined (userspace footprint)"
echo ""
echo "| Metric | MiB |"
echo "|---|---:|"
printf "| Σ PSS across $PROC_COUNT sampled processes | %s |\n" "$(to_mib "$USR_PSS_KB")"
printf "| Σ RSS (inflated by shared-page double-count) | %s |\n" "$(to_mib "$USR_RSS_KB")"
printf "| Σ Swap (may duplicate shared swap entries) | %s |\n" "$(to_mib "$USR_SWAP_KB")"
printf "| Σ proportional swap (SwapPss) | %s |\n" "$(to_mib "$USR_SWAP_PSS_KB")"
echo '> Process PSS includes mapped file cache and shared memory, excludes kernel allocations and HugeTLB, and is sampled over time. Subtracting it from free(1) used does not produce a valid kernel footprint.'
echo ""

# ─────────────────────────────────────────────────────────────────────────────
# SECTION 5 — HYPRLAND-SPECIFIC DIAGNOSTICS
# ─────────────────────────────────────────────────────────────────────────────
echo "## 5. Wayland & Hyprland Diagnostics"
echo "---"
echo '> **Understanding this section:** Interrogates the Wayland compositor directly (using JSON) to see if window surfaces, unmapped layers, or headless monitors are building up in the background.'
echo ""
HYPR_PID=$(pgrep -u "$TARGET_UID" -x Hyprland 2>/dev/null | sed -n '1,1p' || true)
if [[ -n "$HYPR_PID" ]] && command -v hyprctl >/dev/null && command -v jq >/dev/null; then
    HYPR_USER=$TARGET_USER
    HYPR_UID=$TARGET_UID
    echo "- **Hyprland PID:** \`$HYPR_PID\`"
    echo "- **Session User:** \`$HYPR_USER\` (UID: $HYPR_UID)"
    echo '> Compositor RSS/PSS are in the saved process table below; IPC is queried later.'

    echo ""
    HYPR_ENV=("XDG_RUNTIME_DIR=/run/user/$HYPR_UID")
    HYPR_SIG=$(sudo -u "#$HYPR_UID" env "${HYPR_ENV[@]}" hyprctl -j instances 2>/dev/null | jq -r --argjson pid "$HYPR_PID" '.[] | select(.pid == $pid) | .instance' || true)
    HYPR_ENV+=("HYPRLAND_INSTANCE_SIGNATURE=${HYPR_SIG:-unavailable}")

    for query in clients layers monitors; do
        echo "### Hyprland $query (JSON)"
        echo '```json'
        if query_result=$(sudo -u "$HYPR_USER" env "${HYPR_ENV[@]}" hyprctl -j "$query" 2>/dev/null); then
            printf '%s\n' "$query_result" | jq -e . || echo 'Invalid IPC JSON.'
        else
            echo 'IPC unavailable.'
        fi
        echo '```'
    done
else
    echo "**Target user Hyprland process or IPC tools unavailable.**"
fi

echo ""
echo "### Wayland Compositor & Daemon RSS Summary"
echo ""
echo "| Process | PID | RSS (MiB) |"
echo "|---|---|---|"
PROCS=(Hyprland waybar xdg-desktop-portal xdg-desktop-portal-hyprland xdg-desktop-portal-gtk xdg-desktop-portal-gnome pipewire wireplumber hypridle hyprlock swaybg swww-daemon mako dunst fnott eww ags)
python - "$WORK_DIR/processes.json" "${PROCS[@]}" <<'DAEMONS'
import json, sys
wanted=set(sys.argv[2:])
for row in json.load(open(sys.argv[1])):
    if row['comm'] in wanted or row['executable'] in wanted:
        name=row['executable'] if row['executable'] in wanted else row['comm']
        print(f"| {name} | {row['pid']} | {row.get('Rss',0)/1024:.1f} |")
DAEMONS
echo ""

# ─────────────────────────────────────────────────────────────────────────────
# SECTION 6 — SHARED MEMORY / TMPFS
# ─────────────────────────────────────────────────────────────────────────────
echo "## 6. Shared Memory & Tmpfs"
echo "---"
echo '> **Understanding this section:** Tmpfs/shmem can be swapped. File sizes may be sparse; allocated filesystem blocks and mount usage are not resident RAM measurements. Deleted open files and memfd/SysV objects need not appear in /dev/shm.'
echo ""
echo "### Overall Tmpfs Mounts"
echo ""
echo "\`\`\`text"
df -h -t tmpfs 2>&1 || echo 'Tmpfs mount usage unavailable.'
echo "\`\`\`"
echo ""
echo "### /dev/shm Contents (Top 20: disk usage)"
echo ""
echo "\`\`\`text"
find /dev/shm -mindepth 1 -maxdepth 1 -exec du -sh {} + 2>/dev/null | sort -hr | head -n 20 || true
echo "\`\`\`"
echo '> **Note:** High tmpfs/PSS usage warrants checking ownership and growth over time; neither proves a Wayland leak.'
echo ""
echo "### SysV Shared Memory (bytes; not necessarily resident)"
echo ""
echo '```text'
cat /proc/sysvipc/shm 2>/dev/null || echo 'SysV shared memory statistics unavailable.'
echo '```'
echo ""
echo "### XDG_RUNTIME_DIR Allocated File Blocks and Wayland Sockets"
for uid_dir in /run/user/*/; do
    [[ -d "$uid_dir" ]] || continue
    uid="${uid_dir%/}"
    uid="${uid##*/}"
    uname_for_uid=$(getent passwd "$uid" 2>/dev/null | cut -d: -f1 || echo "uid:$uid")

    size=$( (set +e +o pipefail; du -sh "$uid_dir" 2>/dev/null | awk '{print $1}') )
    [[ -z "$size" ]] && size="?"

    wl_socks=$( (set +e +o pipefail; find "$uid_dir" -maxdepth 1 -type s -name 'wayland-*' 2>/dev/null | wc -l) )
    echo "- User **$uname_for_uid** ($uid): \`$size\` in tmpfs, \`$wl_socks\` wayland socket(s)"
done
echo ""

# ─────────────────────────────────────────────────────────────────────────────
# SECTION 7 — KERNEL SLAB LEAK DETECTION
# ─────────────────────────────────────────────────────────────────────────────
echo "## 7. Kernel Slab Objects (Top 15 by Total Memory)"
echo "---"
echo '> **Understanding this section:** The Linux Kernel maintains its own internal RAM caches (Slabs) for things like file structures, network sockets, and inodes. Cache growth can be normal; compare repeated reports under similar workloads before diagnosing a leak.'
echo ""
if [[ -r /proc/slabinfo ]]; then
    echo "\`\`\`text"
    echo "NAME                       NUM_OBJS  OBJSIZE  SLAB_MiB"
    echo "------------------------------------------------------"

    (
        set +e +o pipefail
        awk -v page_kb="$PAGE_KB" 'NR>2 && NF>=15 {
            print $1, $3, $4, ($15 * $6 * page_kb)/1024
        }' /proc/slabinfo | sort -k4 -rn | sed -n '1,15p' | awk '{
            printf "%-26s %9d  %7d  %7.1f\n", $1, $2, $3, $4
        }'
    ) || true
    echo "\`\`\`"

    SLAB_PAYLOAD_MIB=$(awk 'NR>2 && NF>=4 {total += $3 * $4} END {printf "%.0f", total/1048576}' /proc/slabinfo)
    echo "> **Object data inside slab caches (num_objs × objsize):** $SLAB_PAYLOAD_MIB MiB"
    echo "> *This is the object payload only. The authoritative slab total (including per-slab page overhead) is \`Slab\` = **$(mem_mib Slab) MiB** in Section 1. Sustained growth under comparable workloads is worth investigating; cache growth alone does not prove a leak.*"
else
    echo '/proc/slabinfo not readable. Falling back to slabtop:'
    echo "\`\`\`text"
    slabtop -o -s c 2>/dev/null | sed -n '1,20p' || echo "slabtop unavailable."
    echo "\`\`\`"
fi
echo ""

# ── 7b. KSM (Kernel Samepage Merging) ───────────────────────────────────────
echo "### 7b. KSM — Kernel Samepage Merging"
KSM_DIR=/sys/kernel/mm/ksm
if [[ -d "$KSM_DIR" ]]; then
    echo '```text'
    for metric in "$KSM_DIR"/*; do
        [[ ! -f "$metric" || ! -r "$metric" ]] || printf '%s: %s\n' "${metric##*/}" "$(<"$metric")"
    done
    echo '```'
    echo '> run=0 stops scanning but retains merged pages; run=2 requests unmerging. Gross savings = (pages_sharing + ksm_zero_pages) × runtime page size. general_profit is the kernel net estimate in bytes (includes tracking overhead).'
else
    echo 'KSM statistics unavailable on this kernel.'
fi
echo ""

# ── 7c. Loaded Modules (resident footprint) ─────────────────────────────────
echo "### 7c. Loaded Kernel Modules (resident RAM)"
echo ""
echo '```text'
if [[ -r /proc/modules ]]; then
    awk '{n++; bytes+=$2} END {printf "Loaded: %d; sum of module-reported sizes: %.1f MiB\n", n, bytes/1048576}' /proc/modules
    sort -k2,2nr /proc/modules | sed -n '1,10p' | awk '{printf "%-24s %8.1f KiB  references=%s\n", $1, $2/1024, $3}'
else
    echo 'Module list unavailable.'
fi
echo '```'
echo '> Module-reported sizes are not a separate physical-RAM ledger; module mappings may already appear in vmalloc.'
echo ""

# ── 7d. Top vmalloc Allocations ─────────────────────────────────────────────
echo "### 7d. Largest Physically-Backed vmalloc Allocations"
if [[ -r /proc/vmallocinfo ]]; then
    echo "- VmallocUsed snapshot: $(mem_mib VmallocUsed) MiB. The later listing may include mappings of already-accounted physical pages."
    echo ""
    echo "Top 10 (physically backed):"
    echo "\`\`\`text"
    (
        set +e +o pipefail
        awk -v page_kb="$PAGE_KB" '!/ioremap/ {
            for (i = 1; i <= NF; i++) if ($i ~ /^pages=[0-9]+$/) {
                split($i, a, "=")
                caller = "?"
                for (j = 3; j < i; j++) {
                    if ($j ~ /\+0x/) {
                        caller = $j
                        if ($(j+1) ~ /^\[.*\]$/) caller = caller " " $(j+1)
                        break
                    }
                }
                print a[2]*page_kb, caller
            }
        }' /proc/vmallocinfo \
            | sort -rn | sed -n '1,10p' | awk '{ printf "  %10.1f MiB  %s\n", $1/1024, $2 ($3 ? " " $3 : "") }'
    ) || true
    echo "\`\`\`"
else
    echo "- \`/proc/vmallocinfo\` not readable (lockdown/kernel param). Skipping."
fi
echo ""

# ── 7e. Page Allocation Fragmentation Snapshot ──────────────────────────────
echo "### 7e. Page Allocation Fragmentation"
echo "> *High-order contiguous blocks matter for THP/DMA. If order-0 free RAM is plentiful but higher orders are starved, allocations stall despite 'available' memory.*"
echo ""
echo "\`\`\`text"
(
    set +e +o pipefail
    grep -E "^Node" /proc/buddyinfo 2>/dev/null | sed 's/^/  buddyinfo: /'
    sed 's/^/  pagetypeinfo: /' /proc/pagetypeinfo 2>/dev/null
    COMPACT=$(grep -E "^compact_(stall|fail|success)" /proc/vmstat 2>/dev/null)
    [[ -n "$COMPACT" ]] && printf '  vmstat: %s\n' "${COMPACT//$'\n'/$'\n  vmstat: '}"
) || true
echo "\`\`\`"
echo ""

# ─────────────────────────────────────────────────────────────────────────────
# SECTION 8 — DMA-BUF GPU BUFFERS (AQUAMARINE)
# ─────────────────────────────────────────────────────────────────────────────
echo "## 8. GPU DMA-BUF Allocations (Aquamarine / Graphics)"
echo "---"
echo '> **Understanding this section:** DMA-BUFs are shared buffer objects used by GPUs and other devices. Reported size is logical capacity, not necessarily resident system RAM; buffers can live in VRAM and overlap process/shmem/driver counters.'
echo ""

python - <<'DMABUFS'
from collections import Counter
from pathlib import Path
info=Path('/sys/kernel/debug/dma_buf/bufinfo')
sysfs=Path('/sys/kernel/dmabuf/buffers')
rows=[]
failures=0
source=None
try:
    raw=info.read_text()
except OSError:
    raw=None
if raw is not None:
    source='debugfs'
    for line in raw.splitlines():
        fields=line.split()
        if len(fields)>=7 and fields[0].isdigit() and fields[5].isdigit():
            rows.append((int(fields[0]), fields[4]))
    if not rows and 'Total 0 objects' not in raw:
        print('Debugfs returned no parsable buffers; format/coverage unknown.')
elif sysfs.is_dir():
    source='sysfs'
    try:
        for directory in sysfs.iterdir():
            try:
                rows.append((int((directory/'size').read_text()),(directory/'exporter_name').read_text().strip()))
            except (OSError,ValueError):
                failures+=1
    except OSError:
        failures+=1
if source is None:
    print('DMA-BUF global statistics unavailable; debugfs is not mounted automatically. DRM fdinfo below is a separate process view.')
else:
    print(f'Source: {source}; parsed {len(rows)} buffers, logical size {sum(size for size,_ in rows)/1048576:.3f} MiB; failed/exited reads: {failures}.')
    print()
    print('| Largest logical buffers (MiB) | Exporter |')
    print('|---:|---|')
    for size,exporter in sorted(rows,reverse=True)[:10]:
        print(f"| {size/1048576:.3f} | {exporter.replace('|','-')} |")
    print()
    print('| Exporter | Count | Logical MiB |')
    print('|---|---:|---:|')
    counts=Counter(exporter for _,exporter in rows)
    sizes=Counter()
    for size,exporter in rows: sizes[exporter]+=size
    for exporter,count in counts.most_common():
        print(f"| {exporter.replace('|','-')} | {count} | {sizes[exporter]/1048576:.3f} |")
DMABUFS
echo ""

# Intel i915 GEM system-RAM objects (beyond exported DMA-BUFs)
GEM_FILE=$(find /sys/kernel/debug/dri -maxdepth 2 -name i915_gem_objects 2>/dev/null | sed -n '1,1p' || true)
if [[ -n "$GEM_FILE" && -r "$GEM_FILE" ]]; then
    echo "### i915 GEM Object Statistics (driver-specific units)"
    echo '```text'
    cat "$GEM_FILE" 2>/dev/null || echo 'Unavailable during read.'
    echo '```'
fi

echo "### Standardized DRM Client Memory (sampled with process scan)"
echo '> Clients are deduplicated by driver/device/client ID. Counter categories overlap and shared buffers can occur in several clients; no global sum is inferred.'
cat "$WORK_DIR/drm.md"

# Generic per-driver GEM client accounting (i915/amdgpu/virtio_gpu/xe/…)
for dri_dir in /sys/kernel/debug/dri/[0-9]*; do
    [[ -r "$dri_dir/clients" ]] || continue
    echo ""
    echo "### DRM GEM Clients ($dri_dir)"
    echo "> *Driver client list; presence of a client does not quantify its RAM or Unevictable charge.*"
    echo "\`\`\`text"
    cat "$dri_dir/clients" 2>/dev/null | sed 's/^/  /' || true
    echo "\`\`\`"
done

# udmabuf check
if [[ -d /sys/kernel/debug/udmabuf ]]; then
    echo ""
    echo "### udmabuf pools (Zero-copy IPC)"
    echo "\`\`\`text"
    ls -la /sys/kernel/debug/udmabuf/ 2>/dev/null || true
    echo "\`\`\`"
fi

# Dedicated GPU Memory Diagnostics
if command -v nvidia-smi >/dev/null 2>&1; then
    if NVDATA=$(nvidia-smi --query-gpu=index,name,memory.total,memory.used,memory.free --format=csv,noheader,nounits 2>/dev/null); then
        echo "### NVIDIA Dedicated GPU Memory (MiB)"
        echo '```text'
        echo 'index, name, total, used, free'
        printf '%s\n' "$NVDATA"
        echo '```'
    else
        echo 'NVIDIA query unavailable.'
    fi
fi

echo "### DRM Device Memory Counters (bytes, driver-specific)"
echo ""
echo '```text'
for card in /sys/class/drm/card[0-9]*; do
    [[ ${card##*/} =~ ^card[0-9]+$ ]] || continue
    echo "${card##*/}:"
    if [[ -e "$card/device/driver" ]]; then
        printf '  driver: %s\n' "$(readlink -f "$card/device/driver")"
    fi
    for metric in "$card"/device/mem_info_*; do
        [[ ! -r "$metric" ]] || printf '  %s: %s\n' "${metric##*/}" "$(<"$metric")"
    done
done
echo '```'
echo '> Driver counters can describe dedicated VRAM or system-memory domains; compare their documented meaning rather than adding them to RAM totals.'
echo ""

# ─────────────────────────────────────────────────────────────────────────────
# SECTION 9 — TRANSPARENT HUGEPAGES (THP)
# ─────────────────────────────────────────────────────────────────────────────
echo "## 9. Transparent Hugepages (THP) Policies and Residency"
echo "---"
echo '> **Understanding this section:** THP reduces page-table/TLB overhead and can allocate more physical memory than a workload touches. Supported sizes depend on architecture and policy. Resident hugepages remain real memory in both RSS and PSS; PSS only apportions sharing.'
echo ""
THP_DIR=/sys/kernel/mm/transparent_hugepage
echo "- **THP Policy (Enabled):** \`$(cat "$THP_DIR"/enabled 2>/dev/null || echo 'N/A')\`"
echo "- **THP Defrag Policy:** \`$(cat "$THP_DIR"/defrag 2>/dev/null || echo 'N/A')\`"
echo "- **Khugepaged Scans:** \`$(cat "$THP_DIR"/khugepaged/pages_to_scan 2>/dev/null || echo 'N/A')\`"
echo ""
echo "- **AnonHugePages (PMD-sized):** $(mem_mib AnonHugePages) MiB"
echo "- **ShmemHugePages:** $(mem_mib ShmemHugePages) MiB"
echo "- **FileHugePages:** $(mem_mib FileHugePages) MiB"
echo ""

echo "### Per-size THP Policies and Statistics (raw counters)"
echo ""
echo '```text'
for tier in "$THP_DIR"/hugepages-*kB; do
    echo "${tier##*/}:"
    for metric in "$tier"/enabled "$tier"/shmem_enabled "$tier"/stats/*; do
        [[ ! -r "$metric" ]] || printf '  %s: %s\n' "${metric#"$tier"/}" "$(<"$metric")"
    done
done
echo '```'

# HugeTLB reserved pool
HUGETLB_KB=${MEM[Hugetlb]:-0}
HP_TOTAL=${MEM[HugePages_Total]:-0}
if [[ $HP_TOTAL -gt 0 || $HUGETLB_KB -gt 0 ]]; then
    echo ""
    echo "### HugeTLB Reserved Pool"
    echo "\`\`\`text"
    (
        set +e +o pipefail
        printf "  HugeTLB total: %s kB ; default-size pool has %s pages\n" "$HUGETLB_KB" "$HP_TOTAL"
        for hp_dir in /sys/kernel/mm/hugepages/hugepages-*kB; do
            [[ -d "$hp_dir" ]] || continue
            printf "  %-28s total=%s free=%s surplus=%s\n" "$(basename "$hp_dir")" \
                "$(cat "$hp_dir/nr_hugepages" 2>/dev/null)" \
                "$(cat "$hp_dir/free_hugepages" 2>/dev/null)" \
                "$(cat "$hp_dir/surplus_hugepages" 2>/dev/null)"
        done
    ) || true
    echo "\`\`\`"
fi

echo ""
echo '> **Note:** THP memory is included in the underlying anonymous/file/shmem counters. HugeTLB is a distinct reserved pool; its process mappings are omitted from RSS/PSS and shown separately in the process appendix.'
echo ""

# ─────────────────────────────────────────────────────────────────────────────
# SECTION 10 — HYPRLAND MEMORY LEAK CHECKLIST
# ─────────────────────────────────────────────────────────────────────────────
echo "## 10. Hyprland Rendering Diagnostics"
echo "---"

echo "### A. Headless Outputs"
if [[ -n "${HYPR_USER:-}" ]]; then
    HEADLESS=$(sudo -u "$HYPR_USER" env "${HYPR_ENV[@]}" hyprctl monitors all -j 2>/dev/null | jq -r '[.[] | select(.name | ascii_downcase | contains("headless"))] | length' 2>/dev/null || echo unavailable)
    if [[ "$HEADLESS" == unavailable ]]; then
        echo "Headless output query unavailable."
    elif [[ "$HEADLESS" -gt 0 ]]; then
        echo "**Headless outputs detected: $HEADLESS.**"
        echo "Headless outputs may be intentional. A single snapshot cannot establish a buffer leak."
    else
        echo "✅ No headless monitors detected."
    fi
else
    echo "⚠️ Cannot check headless outputs (Hyprland user context missing)."
fi

echo ""
echo "### B. Screencopy / OBS / Portals"
SC_PIDS_OUT=$(pgrep -af 'screencopy|wlr-randr|\bobs\b|sunshine|xdg-desktop-portal|hyprshot|grim|slurp|wl-screenrec' 2>/dev/null | grep -v -E "pgrep|ram_usage" || true)
if [[ -n "$SC_PIDS_OUT" ]]; then
    echo "Active screencasting/portal processes (Presence alone does not prove active capture or pinned buffers):"
    echo "\`\`\`text"
    printf '  %s\n' "${SC_PIDS_OUT//$'\n'/$'\n  '}"
    echo "\`\`\`"
else
    echo "✅ No screen capturing software detected."
fi

echo ""
echo "### C. Decorations & Shadows (Dynamic IPC)"
if [[ -n "${HYPR_USER:-}" ]]; then
    for option in decoration:blur:enabled decoration:shadow:enabled; do
        if value=$(sudo -u "$HYPR_USER" env "${HYPR_ENV[@]}" hyprctl getoption "$option" -j 2>/dev/null | jq -er '(if .bool != null then .bool elif .int != null then .int else error("missing value") end) | tostring'); then
            echo "- $option: $value (rendering cost depends on workload)."
        else
            echo "- $option: unavailable."
        fi
    done
else
    echo "⚠️ Cannot check decorations (Hyprland user context missing)."
fi
echo ""

# ─────────────────────────────────────────────────────────────────────────────
# SECTION 11 — MEMORY PRESSURE EVENTS (OOM)
# ─────────────────────────────────────────────────────────────────────────────
echo "## 11. Memory Pressure Events (OOM History)"
echo "---"

echo "### OOM Kills in Kernel Log"
echo ""
echo "\`\`\`text"
if OOM_LOG=$(dmesg --time-format reltime 2>/dev/null); then
    OOM_SOURCE=dmesg
elif OOM_LOG=$(journalctl -k -b --no-pager -q 2>/dev/null); then
    OOM_SOURCE=journal
else
    OOM_SOURCE=unavailable
    OOM_LOG=""
fi
if [[ "$OOM_SOURCE" == unavailable ]]; then
    echo 'Kernel OOM history unavailable.'
else
    OOM_MATCHES=$(printf '%s\n' "$OOM_LOG" | grep -iE 'invoked oom-killer|oom-kill:|oom_reaper|killed process|out of memory|memory cgroup out of memory' | tail -10 || true)
    printf '%s\n' "${OOM_MATCHES:-No matching OOM events in the readable current-boot log.}"
fi
echo "\`\`\`"

echo "### Userspace OOM Daemon (systemd-oomd & Slices)"
echo ""
echo "\`\`\`text"
if command -v oomctl >/dev/null 2>&1; then
    oomctl --no-pager 2>&1 || echo 'oomctl unavailable or daemon inactive.'
else
    echo "  systemd-oomd not active or oomctl missing."
fi
echo ""
for svc in systemd-oomd.service systemd-journald.service; do
    mh=$(systemctl show "$svc" -p MemoryHigh --value 2>/dev/null || echo "")
    mm=$(systemctl show "$svc" -p MemoryMax --value 2>/dev/null || echo "")
    [[ -n "$mh" && "$mh" != "infinity" ]] && echo "  [$svc] MemoryHigh limits active: $mh"
    [[ -n "$mm" && "$mm" != "infinity" ]] && echo "  [$svc] MemoryMax limits active: $mm"
done
echo "\`\`\`"
echo ""

echo "### Pressure Stall Information (PSI)"
echo ""
echo "\`\`\`text"
for res in memory cpu io; do
    PSI_FILE="/proc/pressure/$res"
    if [[ -r "$PSI_FILE" ]]; then
        echo "${res}:"
        sed 's/^/  /' "$PSI_FILE"
    else
        echo "$res: PSI unavailable."
    fi
done
echo "\`\`\`"
echo ""

# ─────────────────────────────────────────────────────────────────────────────
# SECTION 11b — EMPIRICAL RECLAIMABILITY SELF-TEST (--probe, opt-in)
# ─────────────────────────────────────────────────────────────────────────────
if [[ "$PROBE" == true ]]; then
    echo "## 11b. Reclaimability Self-Test (\`--probe\`) — MEASURED, not assumed"
    echo "---"
    echo "> *Executes \`sync\` + \`echo 3 > /proc/sys/vm/drop_caches\` and measures which memory buckets actually shrink. Transiently evicts clean page cache/slab; may substantially slow subsequent disk access. Shows observed change; unchanged buckets do not prove pinning, and concurrent activity prevents causal attribution.*"
    echo ""
    sync
    probe_snap() {
        awk '
        $1=="MemFree:"{f=$2} $1=="Cached:"{c=$2} $1=="Shmem:"{sh=$2}
        $1=="Slab:"{sl=$2} $1=="Unevictable:"{u=$2} $1=="AnonPages:"{a=$2}
        END { printf "%d %d %d %d %d %d", f, c, sh, sl, u, a }' /proc/meminfo
    }
    read -r PF0 PC0 PS0 PSB0 PU0 PA0 <<< "$(probe_snap)"
    if ! echo 3 > /proc/sys/vm/drop_caches; then
        echo "Cache drop failed; probe aborted." >&2
        exit 1
    fi
    sleep 3
    read -r PF1 PC1 PS1 PSB1 PU1 PA1 <<< "$(probe_snap)"
    delta() { echo $(( $2 - $1 )); }
    echo "| Bucket | Before (MiB) | After+3s (MiB) | Δ (MiB) | Verdict |"
    echo "|---|---:|---:|---:|---|"
    verdict_row() {
        local name="$1"
        local b="$2"
        local a="$3"
        local d="$4"
        local v="no significant change; reclaimability undetermined"
        local delta_mib
        if [[ "$name" == "MemFree" ]]; then
            delta_mib=$(to_mib "$d")
            if (( d > 2048 )); then
                v="GAINED free RAM after cache drop"
            elif (( d < -2048 )); then
                v="LOST free RAM during observation"
            fi
        else
            delta_mib=$(to_mib "$d")
            if (( d < -2048 )); then
                v="DECREASED after cache drop (concurrent workload may contribute)"
            elif (( d > 2048 )); then
                v="GREW during observation"
            fi
        fi
        printf "| %s | %s | %s | %s | %s |\n" "$name" "$(to_mib "$b")" "$(to_mib "$a")" "$delta_mib" "$v"
    }
    verdict_row "MemFree"     "$PF0"  "$PF1"  "$(delta "$PF0" "$PF1")"
    verdict_row "Cached"      "$PC0"  "$PC1"  "$(delta "$PC0" "$PC1")"
    verdict_row "Slab"        "$PSB0" "$PSB1" "$(delta "$PSB0" "$PSB1")"
    verdict_row "Unevictable" "$PU0"  "$PU1"  "$(delta "$PU0" "$PU1")"
    verdict_row "AnonPages"   "$PA0"  "$PA1"  "$(delta "$PA0" "$PA1")"
    verdict_row "Shmem"       "$PS0"  "$PS1"  "$(delta "$PS0" "$PS1")"
    echo ""
fi

# ─────────────────────────────────────────────────────────────────────────────
# SECTION 12 — QUICK DIAGNOSIS GUIDE
# ─────────────────────────────────────────────────────────────────────────────
echo "## 12. Quick Diagnosis Guide"
echo "---"
cat <<'GUIDE'
- Start with MemAvailable and memory PSI. Swap usage alone does not imply current pressure.
- Compare repeated reports under comparable workloads. A large allocation alone is not a leak.
- Use PSS and the full process appendix to locate mapped-memory consumers; HugeTLB and driver memory require separate views.
- Use cgroup limits/events to distinguish a service-level OOM from global exhaustion.
- Inspect slab, tmpfs, GPU and compression trends without adding overlapping totals.
- Fragmentation concerns contiguous allocations; it is not an extra RAM sink.
GUIDE

echo ""
echo "## 13. Kernel Measurement Limits"
echo '> Custom-kernel savings require controlled before/after boots with comparable workloads. Module sizes, vmalloc pages and reclaimable caches do not justify fixed percentage savings. No savings projection is inferred from this snapshot.'
echo ""
echo "## Appendix A. Every Sampled Process (PSS order)"
cat "$WORK_DIR/pss.md"
echo ""
echo "## Appendix B. Full vmstat Snapshot (original units)"
echo ""
echo '```text'
printf '%s\n' "$VMSTAT"
echo '```'
echo ""
echo "## Appendix C. Raw DRM fdinfo Counters and Holders"
echo ""
echo '```json'
cat "$WORK_DIR/drm.json"
echo '```'
echo ""
echo "***"
echo "**END OF FORENSICS REPORT**"
echo "***"

} 2>&1 | tee "$REPORT"

echo -e "\n\e[1;32m[✓] Analysis complete. Markdown report written to:\e[0m"
echo -e "\e[1;36m$REPORT\e[0m"
