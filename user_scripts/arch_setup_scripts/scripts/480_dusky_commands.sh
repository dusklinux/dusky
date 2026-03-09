#!/usr/bin/env bash
# ==============================================================================
#  DUSKY FLEET PATCHER (Enterprise Edition - V3)
#  Description: Idempotent, concurrency-safe fleet orchestrator.
#  Target:      Arch Linux / Hyprland / UWSM / Bash 5.3+
# ==============================================================================

# 1. Strict Safety Mode
set -o errexit
set -o nounset
set -o pipefail

# ==============================================================================
#  USER CONFIGURATION AREA — Define Fleet Commands Here
# ==============================================================================
# FORMAT: "MODE | COMMAND"
#   MODE 'U': Runs as the current user.
#   MODE 'S': Runs as root via sudo.
#
# PRO TIP: Append ' || true' to commands that might fail on some hardware but
# shouldn't halt the patch sequence (e.g., removing a non-existent cache).
# double quotes will work too but single quotes allows for also having a set of double quites withing the command eg:
# 'U | gsettings set org.gnome.desktop.interface icon-theme "Papirus"'

declare -ra FLEET_COMMANDS=(
    'U | gsettings set org.gnome.desktop.interface icon-theme "Papirus"'
    'U | gsettings set org.gnome.desktop.interface gtk-theme "adw-gtk3"'
    'U | gsettings set org.cinnamon.desktop.default-applications.terminal exec "kitty"'
    'U | mkdir -p "$HOME/.config/gtk-3.0" "$HOME/.config/gtk-4.0"'
    'U | ln -nfs "$HOME/.config/matugen/generated/gtk-3.css" "$HOME/.config/gtk-3.0/gtk.css"'
    'U | ln -nfs "$HOME/.config/matugen/generated/gtk-4.css" "$HOME/.config/gtk-4.0/gtk.css"'
    'U | ln -nfs "/usr/share/themes/adw-gtk3/gtk-4.0/libadwaita.css" "$HOME/.config/gtk-4.0/libadwaita.css"'
    'U | ln -nfs "/usr/share/themes/adw-gtk3/gtk-4.0/libadwaita-tweaks.css" "$HOME/.config/gtk-4.0/libadwaita-tweaks.css"'
    'U | "$HOME/user_scripts/dusky_system/reload_cc/cc_restart.sh" --quiet &'
    'U | "$HOME/user_scripts/sliders/reload_sliders/reload_sliders.sh" --quiet &'

    # --- System Services ---
#    "U | systemctl --user disable dusky.service || true"
#    "S | systemctl enable --now tlp.service || true"

)

# ==============================================================================
#  INTERNAL ENGINE (Do not edit below)
# ==============================================================================

# 2. Paths & Constants
readonly STATE_DIR="${HOME}/.local/state/dusky"
readonly STATE_FILE="${STATE_DIR}/patch_history.state"
readonly LOG_FILE="${HOME}/Documents/logs/dusky_patcher_$(date +%Y%m%d_%H%M%S).log"
readonly LOCK_FILE="${XDG_RUNTIME_DIR:-/run/user/$UID}/dusky_fleet_patcher.lock"
readonly SUDO_REFRESH_INTERVAL=50

# 3. Global Variables
declare -g SUDO_PID=""
declare -gA COMPLETED_PATCHES=()
declare -g LOGGING_INITIALIZED=0
declare -g LOG_PID=""

# 4. Terminal Colors
declare -g RED="" GREEN="" BLUE="" YELLOW="" BOLD="" RESET=""
if [[ -t 1 ]]; then
    RED=$'\e[1;31m' GREEN=$'\e[1;32m' YELLOW=$'\e[1;33m' BLUE=$'\e[1;34m'
    BOLD=$'\e[1m' RESET=$'\e[0m'
fi

# 5. Logging Subsystem
setup_logging() {
    mkdir -p "$(dirname "$LOG_FILE")" "${STATE_DIR}"
    touch "$LOG_FILE" "$STATE_FILE"
    
    # Close FD 9 explicitly inside subshells to prevent lock leaks
    exec > >(exec 9>&-; tee >(exec 9>&-; sed 's/\x1B\[[0-9;]*[a-zA-Z]//g; s/\x1B(B//g' >> "$LOG_FILE")) 2>&1
    
    # Capture the exact PID of the process substitution for deterministic waiting
    LOG_PID=$!
    LOGGING_INITIALIZED=1
    log "INFO" "Fleet Patcher Initialized: $(date '+%Y-%m-%d %H:%M:%S')"
}

log() {
    local level="$1" msg="$2" color=""
    case "$level" in
        INFO)    color="$BLUE" ;;
        SUCCESS) color="$GREEN" ;;
        WARN)    color="$YELLOW" ;;
        ERROR)   color="$RED" ;;
        RUN)     color="$BOLD" ;;
    esac
    printf "%s[%s]%s %s\n" "${color}" "${level}" "${RESET}" "${msg}"
}

# 6. Privilege Escalation Management
init_sudo() {
    log "INFO" "Root privileges required for upcoming patches. Authenticating..."
    if ! sudo -v; then
        log "ERROR" "Sudo authentication failed. Cannot apply root patches."
        exit 1
    fi

    # Hardened Keepalive
    (
        exec 9>&- # Ensure keepalive subshell doesn't hold the flock
        while kill -0 $$ 2>/dev/null; do
            sleep "$SUDO_REFRESH_INTERVAL"
            sudo -n true 2>/dev/null || exit 0
        done
    ) &
    SUDO_PID=$!
    disown "$SUDO_PID"
}

# 7. Cleanup & I/O Flushing
cleanup() {
    # FIX: Capture the incoming exit code BEFORE executing any commands that reset $?
    local exit_code=$?
    set +o errexit
    
    [[ -n "${SUDO_PID:-}" ]] && kill "$SUDO_PID" 2>/dev/null || true

    if [[ $LOGGING_INITIALIZED -eq 1 ]]; then
        if (( exit_code == 0 )); then
            log "SUCCESS" "All fleet patches verified and up to date."
        fi
        
        # Close file descriptors to signal the tee/sed pipe to finish
        exec 1>&- 2>&-
        
        # Deterministically wait for the logger to flush data to disk
        [[ -n "$LOG_PID" ]] && wait "$LOG_PID" 2>/dev/null || true
    fi
    exit "$exit_code"
}
trap cleanup EXIT

# 8. State Management (O(1) Memory Array)
load_state() {
    if [[ -s "$STATE_FILE" ]]; then
        local _lines=()
        local _line
        mapfile -t _lines < "$STATE_FILE" 2>/dev/null || true
        for _line in "${_lines[@]}"; do
            [[ -n "$_line" ]] && COMPLETED_PATCHES["$_line"]=1
        done
    fi
}

# 9. Main Execution Engine
main() {
    # Root Guard
    if [[ $EUID -eq 0 ]]; then
        echo -e "${RED}CRITICAL ERROR: Do NOT run this as root! Run as normal user.${RESET}"
        exit 1
    fi

    # Concurrency Lock
    exec 9>"$LOCK_FILE"
    if ! flock -n 9; then
        echo -e "${RED}ERROR: Another patcher instance is already running.${RESET}"
        exit 1
    fi

    setup_logging
    load_state

    # --- Phase 1: Pre-Flight Validation & UX Optimization ---
    local needs_sudo=0
    local -a parsed_modes=()
    local -a parsed_cmds=()
    local -a parsed_hashes=()

    for entry in "${FLEET_COMMANDS[@]}"; do
        [[ "$entry" =~ ^[[:space:]]*# ]] && continue
        [[ -z "${entry//[[:space:]]/}" ]] && continue

        if [[ "$entry" =~ ^[[:space:]]*([SU])[[:space:]]*\|[[:space:]]*(.+)$ ]]; then
            local mode="${BASH_REMATCH[1]}"
            local cmd="${BASH_REMATCH[2]}"
            local entry_orig="${mode} | ${cmd}"
            
            # FIX: Generate the hash in Phase 1 to accurately verify state
            local cmd_hash
            read -r cmd_hash _ < <(printf '%s' "$entry_orig" | sha256sum)

            parsed_modes+=("$mode")
            parsed_cmds+=("$cmd")
            parsed_hashes+=("$cmd_hash")

            # FIX: Only flag for sudo if the root command hasn't been executed yet
            if [[ "$mode" == "S" ]] && [[ -z "${COMPLETED_PATCHES[$cmd_hash]:-}" ]]; then
                needs_sudo=1
            fi
        else
            log "ERROR" "Pre-flight validation failed. Invalid format: '$entry'"
            exit 1
        fi
    done

    [[ $needs_sudo -eq 1 ]] && init_sudo

    # --- Phase 2: Execution Engine ---
    local total=${#parsed_cmds[@]}
    
    for (( i=0; i<total; i++ )); do
        local mode="${parsed_modes[i]}"
        local cmd="${parsed_cmds[i]}"
        local cmd_hash="${parsed_hashes[i]}"

        # Utilize the cached Phase 1 hash
        if [[ -n "${COMPLETED_PATCHES[$cmd_hash]:-}" ]]; then
            log "INFO" "[$((i+1))/$total] Skipping (Already applied): $cmd"
            continue
        fi

        log "RUN" "[$((i+1))/$total] Applying [$mode]: $cmd"

        local result=0
        if [[ "$mode" == "S" ]]; then
            sudo bash -c "set -eo pipefail; $cmd" || result=$?
        elif [[ "$mode" == "U" ]]; then
            bash -c "set -eo pipefail; $cmd" || result=$?
        fi

        if (( result == 0 )); then
            printf '%s\n' "$cmd_hash" >> "$STATE_FILE"
            COMPLETED_PATCHES["$cmd_hash"]=1
            log "SUCCESS" "Patch applied successfully."
        else
            log "ERROR" "Patch failed with exit code $result: $cmd"
            log "WARN" "Halting fleet patcher to prevent cascading failures."
            exit 1
        fi
    done
}

main "$@"
