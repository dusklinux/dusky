#!/usr/bin/env bash

# -----------------------------------------------------------------------------
# Script: io-monitor.sh
# Description: Full Disk I/O Dashboard (Educational Edition)
#              Shows RAM Buffers + Lifetime Totals + Instant Write Speed.
#              Supports CLI arguments for instant launch.
# Version: 7.0 (Optimized)
# -----------------------------------------------------------------------------

# Strict Mode - fail fast on errors
set -euo pipefail
shopt -s inherit_errexit 2>/dev/null || true

# --- Configuration & ANSI Colors ---
# Using \e for readability (Bash 3.2+)
readonly C_RESET=$'\e[0m'
readonly C_BOLD=$'\e[1m'
readonly C_CYAN=$'\e[36m'
readonly C_GREEN=$'\e[32m'
readonly C_RED=$'\e[31m'
readonly C_PURPLE=$'\e[35m'
readonly C_GREY=$'\e[90m'

# Regex for valid block device names (letters, numbers, hyphens, underscores)
readonly VALID_DEV_REGEX='^[a-zA-Z0-9_-]+$'

# --- Trap & Cleanup ---
cleanup() {
    # Restore cursor visibility on exit
    tput cnorm 2>/dev/null || true
}
trap cleanup EXIT INT TERM

# --- Utility: Print error and exit ---
die() {
    printf '%s[Error] %s%s\n' "$C_RED" "$1" "$C_RESET" >&2
    exit "${2:-1}"
}

# --- Dependency Check ---
check_deps() {
    local -a missing=()
    local cmd
    
    # iostat is from sysstat package, watch from procps-ng
    for cmd in iostat lsblk watch tput; do
        command -v "$cmd" &>/dev/null || missing+=("$cmd")
    done

    if (( ${#missing[@]} )); then
        die "Missing dependencies: ${missing[*]} (install: sysstat, procps-ng, ncurses)"
    fi
}

# --- Validate Device Name ---
validate_device() {
    local dev="$1"
    
    # Check character validity first (security: prevents injection)
    if [[ ! "$dev" =~ $VALID_DEV_REGEX ]]; then
        die "Invalid device name format: '$dev'"
    fi
    
    # Verify it's an actual block device
    if [[ ! -b "/dev/$dev" ]]; then
        die "Device '/dev/$dev' does not exist or is not a block device."
    fi
}

# --- Drive Selection (Interactive) ---
select_drive() {
    local -a dev_list=()
    local -A dev_set=()  # Associative array for O(1) validation
    local name size type model formatted
    
    # Send UI to stderr (stdout reserved for return value)
    {
        clear
        printf '%s%s:: Drive Selection ::%s\n' "$C_BOLD" "$C_CYAN" "$C_RESET"
        printf '%s%-12s %-10s %-8s %-24s%s\n' "$C_BOLD" "NAME" "SIZE" "TYPE" "MODEL" "$C_RESET"
        printf '%s%s%s\n' "$C_GREY" "────────────────────────────────────────────────────────────" "$C_RESET"
    } >&2

    # Parse lsblk - filter virtual devices
    while read -r name size type model; do
        [[ -z "$name" ]] && continue
        
        dev_list+=("$name")
        dev_set["$name"]=1
        
        printf -v formatted '%-12s %-10s %-8s %-24s' "$name" "$size" "$type" "${model:-N/A}"
        printf '%s%s%s\n' "$C_GREEN" "$formatted" "$C_RESET" >&2
    done < <(lsblk -dno NAME,SIZE,TYPE,MODEL | grep -vE '^(loop|sr|ram|zram|fd)')

    if (( ${#dev_list[@]} == 0 )); then
        die "No physical drives detected."
    fi

    # Prompt with first available device as example
    printf '\n%sEnter target drive (e.g., %s): %s' "$C_BOLD" "${dev_list[0]}" "$C_RESET" >&2
    
    local input
    if ! read -r -t 60 input; then
        printf '\n' >&2
        die "Timed out waiting for input (60s)."
    fi

    # Normalize: strip /dev/ prefix and whitespace
    input="${input#/dev/}"
    input="${input//[[:space:]]/}"

    # O(1) validation using associative array
    if [[ -z "${dev_set[$input]+_}" ]]; then
        die "Invalid device: '$input'. Available: ${dev_list[*]}"
    fi

    # Return selected drive via stdout
    printf '%s' "$input"
}

# --- Build Dashboard Command ---
# Using a function makes the command generation cleaner and testable
build_dashboard_cmd() {
    local drive="$1"
    
    # Note: This string is passed to 'watch' which runs it via sh -c
    # We expand colors and drive name now; escape awk's $ for later evaluation
    cat <<-EOF
	# Section 1: System Write Buffers
	printf '${C_BOLD}${C_CYAN}━━━ 1. System Write Buffer (RAM) ━━━${C_RESET} ${C_GREY}[ grep Dirty|Writeback /proc/meminfo ]${C_RESET}\n'
	grep -E '^(Dirty|Writeback):' /proc/meminfo | awk '{printf "  %-15s %8.2f MB\n", \$1, \$2/1024}'
	
	# Section 2: Lifetime I/O Totals
	printf '\n${C_BOLD}${C_PURPLE}━━━ 2. Lifetime I/O (Since Boot) ━━━${C_RESET} ${C_GREY}[ iostat -m -d /dev/${drive} ]${C_RESET}\n'
	iostat -m -d /dev/${drive} | grep -E '^(Device|${drive})'
	
	# Section 3: Instant Speed (1-second sample)
	printf '\n${C_BOLD}${C_GREEN}━━━ 3. Instant Speed (Last 1s) ━━━${C_RESET} ${C_GREY}[ iostat -y -m -d 1 1 ]${C_RESET}\n'
	iostat -y -m -d /dev/${drive} 1 1 | grep '^${drive}'
	EOF
}

# --- Display Help ---
show_help() {
    cat <<-EOF
	${C_BOLD}Usage:${C_RESET} ${0##*/} [DEVICE]
	
	${C_BOLD}Description:${C_RESET}
	  Monitor disk I/O with a real-time dashboard showing:
	    • RAM write buffers (Dirty/Writeback)
	    • Lifetime I/O statistics (since boot)
	    • Instant read/write speeds (1-second samples)
	
	${C_BOLD}Arguments:${C_RESET}
	  DEVICE    Block device name (e.g., sda, nvme0n1)
	            If omitted, an interactive menu is shown.
	
	${C_BOLD}Examples:${C_RESET}
	  ${0##*/}           # Interactive device selection
	  ${0##*/} sda       # Monitor /dev/sda directly
	  ${0##*/} nvme0n1   # Monitor NVMe drive
	
	${C_BOLD}Dependencies:${C_RESET}
	  iostat (sysstat), watch (procps-ng), lsblk, tput (ncurses)
	EOF
    exit 0
}

# --- Main Entry Point ---
main() {
    # Handle help flag
    [[ "${1:-}" =~ ^(-h|--help)$ ]] && show_help
    
    check_deps
    
    local drive
    
    # Parse command line or launch interactive selection
    if (( $# > 0 )); then
        drive="${1#/dev/}"
        validate_device "$drive"
    else
        drive=$(select_drive)
    fi
    
    # Defensive check (shouldn't happen, but safe)
    [[ -z "$drive" ]] && die "No drive selected."

    # Build the dashboard command
    local dashboard_cmd
    dashboard_cmd=$(build_dashboard_cmd "$drive")

    # Launch sequence
    clear
    printf '%s╔═══════════════════════════════════════════════════════════════╗%s\n' "$C_CYAN" "$C_RESET"
    printf '%s║  %sI/O Dashboard%s :: Monitoring /dev/%-27s%s ║%s\n' "$C_CYAN" "$C_BOLD" "$C_RESET$C_CYAN" "$drive" "$C_CYAN" "$C_RESET"
    printf '%s╚═══════════════════════════════════════════════════════════════╝%s\n' "$C_CYAN" "$C_RESET"
    printf '%sPress Ctrl+C to exit.%s\n\n' "$C_GREY" "$C_RESET"
    sleep 0.8

    # Hide cursor during watch (cleanup trap restores it)
    tput civis 2>/dev/null || true

    # Use exec to replace shell process (cleaner, no zombie)
    # The -- ensures watch doesn't interpret cmd as options
    exec watch --color -t -d -n 1 -- "$dashboard_cmd"
}

# Execute main with all arguments
main "$@"
