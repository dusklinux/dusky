#!/usr/bin/env bash
# Dusky RAM Monitor - Pure Bash Real-Time Memory HUD Daemon
# Forensic Optimization: Zero forks, zero subshells, synchronous D-Bus repaint.

# ==============================================================================
# CONFIGURATION SETTINGS
# ==============================================================================

# Critical physical RAM threshold (% used).
# Triggers warning unconditionally if RAM goes above this limit, even if ZRAM is empty.
THRESHOLD_RAM_CRITICAL=95

# High physical RAM threshold (% used).
# Combined with THRESHOLD_ZRAM_HIGH; both must be met to trigger the warning.
THRESHOLD_RAM_HIGH=90

# High ZRAM Swap occupancy threshold (% used).
# Combined with THRESHOLD_RAM_HIGH; both must be met to trigger the warning.
THRESHOLD_ZRAM_HIGH=90

# RAM Recovery Hysteresis Threshold (% used).
# The HUD dissolves and cooldown starts ONLY if physical RAM drops below this percentage.
THRESHOLD_RAM_RECOVERY=80

# Polling Interval (seconds)
# The wait time between memory scans (supports floating-point sub-second values).
POLL_INTERVAL=0.5

# Cooldown / Grace Interval (seconds)
# The post-recovery grace period during which non-critical alerts are suppressed.
COOLDOWN_SECS=120

# ==============================================================================
# INTERNAL STATE TRACKING (Do not modify)
# ==============================================================================
hud_active=false
grace_expire_time=0

# ==============================================================================
# ENVIRONMENT PREPARATION
# ==============================================================================

# Load Bash's internal C-compiled sleep to prevent forking /usr/bin/sleep
if [[ -f /usr/lib/bash/sleep ]]; then
    enable -f /usr/lib/bash/sleep sleep 2>/dev/null
fi

echo "[$(date '+%Y-%m-%d %H:%M:%S')] Dusky RAM HUD started. Config: CriticalRAM=${THRESHOLD_RAM_CRITICAL}%, HighRAM=${THRESHOLD_RAM_HIGH}%, HighZRAM=${THRESHOLD_ZRAM_HIGH}%, RecoveryRAM=${THRESHOLD_RAM_RECOVERY}%, PollInterval=${POLL_INTERVAL}s" >&2

# ==============================================================================
# MAIN POLLING & HUD LOOP
# ==============================================================================

while true; do
    MemTotal=0
    MemFree=0
    InactiveFile=0
    SReclaimable=0
    
    # 1. Parse RAM stats (Short-circuits at SReclaimable to save CPU cycles)
    while read -r key val _; do
        case "$key" in
            MemTotal:)          MemTotal=$val ;;
            MemFree:)           MemFree=$val ;;
            "Inactive(file):")  InactiveFile=$val ;;
            SReclaimable:)      SReclaimable=$val; break ;;
        esac
    done < /proc/meminfo
    
    Available=$(( MemFree + InactiveFile + SReclaimable ))
    if (( MemTotal > 0 )); then
        RamUsedPct=$(( (MemTotal - Available) * 100 / MemTotal ))
    else
        RamUsedPct=0
    fi
    
    # 2. Parse ZRAM stats (Direct SysFS reads, zero pipes)
    ZramTotal=0
    ZramUsed=0
    
    if [[ -f "/sys/block/zram0/disksize" && -f "/sys/block/zram0/mm_stat" ]]; then
        read -r ZramTotal < /sys/block/zram0/disksize
        read -r ZramUsed _ < /sys/block/zram0/mm_stat
    fi
    
    if (( ZramTotal > 0 )); then
        ZramUsedPct=$(( ZramUsed * 100 / ZramTotal ))
    else
        ZramUsedPct=0
    fi
    
    # 3. State Machine & Threshold Evaluation
    is_breached=0
    if (( RamUsedPct >= THRESHOLD_RAM_CRITICAL || (RamUsedPct >= THRESHOLD_RAM_HIGH && ZramUsedPct >= THRESHOLD_ZRAM_HIGH) )); then
        is_breached=1
    fi

    if (( is_breached )); then
        if [[ "$hud_active" == false ]]; then
            # Evaluate post-recovery grace period
            if (( EPOCHSECONDS < grace_expire_time && RamUsedPct < THRESHOLD_RAM_CRITICAL )); then
                # Suppress non-critical fluctuations while system stabilizes
                :
            else
                hud_active=true
                echo "[$(date '+%Y-%m-%d %H:%M:%S')] HUD ACTIVATED: RAM=${RamUsedPct}%, ZRAM=${ZramUsedPct}%" >&2
            fi
        fi
    elif (( RamUsedPct <= THRESHOLD_RAM_RECOVERY )); then
        if [[ "$hud_active" == true ]]; then
            echo "[$(date '+%Y-%m-%d %H:%M:%S')] SYSTEM RECOVERED: RAM=${RamUsedPct}% (below ${THRESHOLD_RAM_RECOVERY}%)" >&2
            
            # Send final repaint to transition the HUD box to a recovery notice
            /usr/bin/notify-send -a "dusky-high-ram-alert" \
                -h string:x-canonical-private-synchronous:dusky-ram-hud \
                -u normal \
                -t 3000 \
                "SYSTEM RECOVERED" \
                "RAM: ${RamUsedPct}% | Memory Stabilized"
                
            hud_active=false
            grace_expire_time=$(( EPOCHSECONDS + COOLDOWN_SECS ))
        fi
    fi

    # 4. Render Live HUD Frame (Fires twice a second while active)
    if [[ "$hud_active" == true ]]; then
        /usr/bin/notify-send -a "dusky-high-ram-alert" \
            -h string:x-canonical-private-synchronous:dusky-ram-hud \
            -u critical \
            -t 1500 \
            "CRITICAL MEMORY LOW" \
            "RAM: ${RamUsedPct}% | ZRAM: ${ZramUsedPct}%"
    fi
    
    # Pure Bash sleep (if loaded), falling back to binary gracefully
    sleep "$POLL_INTERVAL"
done
