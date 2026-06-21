#!/usr/bin/env bash
# ==============================================================================
#  ARCH LINUX MASTER ORCHESTRATOR
# ==============================================================================
#  INSTRUCTIONS:
#  1. Configure SCRIPT_SEARCH_DIRS below with directories containing your scripts.
#  2. Edit the 'INSTALL_SEQUENCE' list below.
#  3. Use "S | name.sh" for Root (Sudo) commands.
#  4. Use "U | name.sh" for User commands.
#  5. Entries WITHOUT a / in the name are searched across SCRIPT_SEARCH_DIRS
#     in order (first match wins).
#  6. Entries WITH a / are treated as direct paths (no searching).
#     Use ${HOME} instead of ~ for home directory paths.
# ==============================================================================

# --- USER CONFIGURATION AREA ---

# Directories to search for scripts (in order — first match wins)
SCRIPT_SEARCH_DIRS=(
  "${HOME}/user_scripts/dusky_vm/kvm"
)

# Delay (in seconds) after each successful script. Set to 0 to disable.
POST_SCRIPT_DELAY=0

INSTALL_SEQUENCE=(

  "S | 010_kvm_host_setup.py"
  "S | 020_kvm_storage_setup.py"
  "S | 030_kvm_vfio_isolation.py"
  "S | 040_kvm_vm_deploy.py"

)

# ==============================================================================
#  INTERNAL ENGINE (Do not edit below unless you know Bash)
# ==============================================================================

# 1. Safety First
set -o errexit
set -o nounset
set -o pipefail

# 2. Paths & Constants
readonly STATE_FILE="${HOME}/Documents/logs/dusky_vm/.install_state"
readonly LOG_FILE="${HOME}/Documents/logs/dusky_vm/install_$(date +%Y%m%d_%H%M%S).log"
readonly LOCK_FILE="/tmp/orchestra_vm_${UID}.lock"
readonly SUDO_REFRESH_INTERVAL=50

# 3. Global Variables
declare -g SUDO_PID=""
declare -g LOGGING_INITIALIZED=0
declare -g EXECUTION_PHASE=0

# 4. O(1) Arrays
declare -gA COMPLETED_SCRIPTS=()
declare -gA SCRIPT_CACHE=()

# 5. Colors
declare -g RED="" GREEN="" BLUE="" YELLOW="" BOLD="" RESET=""

if [[ -t 1 ]]; then
    RED=$'\e[1;31m'
    GREEN=$'\e[1;32m'
    YELLOW=$'\e[1;33m'
    BLUE=$'\e[1;34m'
    BOLD=$'\e[1m'
    RESET=$'\e[0m'
fi

# 6. Logging
setup_logging() {
    local log_dir
    log_dir="$(dirname "$LOG_FILE")"

    if [[ ! -d "$log_dir" ]]; then
        mkdir -p "$log_dir" || {
            echo "CRITICAL ERROR: Could not create log directory $log_dir" >&2
            exit 1
        }
    fi

    touch "$LOG_FILE"

    # Close FD 9 for the tee process to avoid lock file inheritance
    exec > >(exec 9>&-; tee >(sed 's/\x1B\[[0-9;]*[a-zA-Z]//g; s/\x1B(B//g' >> "$LOG_FILE")) 2>&1

    LOGGING_INITIALIZED=1
    echo "--- Installation Started: $(date '+%Y-%m-%d %H:%M:%S') ---"
    echo "--- Log File: $LOG_FILE ---"
}

log() {
    local level="$1"
    local msg="$2"
    local color=""

    case "$level" in
        INFO)    color="$BLUE" ;;
        SUCCESS) color="$GREEN" ;;
        WARN)    color="$YELLOW" ;;
        ERROR)   color="$RED" ;;
        RUN)     color="$BOLD" ;;
    esac

    printf "%s[%s]%s %s\n" "${color}" "${level}" "${RESET}" "${msg}"
}

# 7. Sudo Management
init_sudo() {
    log "INFO" "Sudo privileges required. Please authenticate."
    if ! sudo -v; then
        log "ERROR" "Sudo authentication failed."
        exit 1
    fi

    # Close FD 9 to prevent the refresh loop from holding the lock
    (
        exec 9>&-
        set +e
        trap 'exit 0' TERM
        while kill -0 "$$" 2>/dev/null; do
            sleep "$SUDO_REFRESH_INTERVAL" &
            wait $! 2>/dev/null || true
            sudo -n -v 2>/dev/null || exit 0
        done
    ) &
    SUDO_PID=$!
    disown "$SUDO_PID"
}

cleanup() {
    local exit_code=$?

    if [[ -n "${SUDO_PID:-}" ]]; then
        kill "$SUDO_PID" 2>/dev/null || true
        wait "$SUDO_PID" 2>/dev/null || true
    fi

    if [[ $EXECUTION_PHASE -eq 1 ]]; then
        if [[ $exit_code -eq 0 ]]; then
            log "SUCCESS" "Orchestrator finished successfully."
        else
            log "ERROR" "Orchestrator exited with error code $exit_code."
        fi
    fi

    exec 9>&- 2>/dev/null || true

    # Allow process substitution (tee/sed) to flush final output to log file
    if [[ $LOGGING_INITIALIZED -eq 1 ]]; then
        sleep 0.3
    fi
}
trap cleanup EXIT

# 8. Utility Functions
trim() {
    local var="$*"
    var="${var#"${var%%[![:space:]]*}"}"
    var="${var%"${var##*[![:space:]]}"}"
    printf '%s' "$var"
}

ensure_state_dir() {
    local state_dir
    state_dir="$(dirname "$STATE_FILE")"

    if [[ ! -d "$state_dir" ]]; then
        mkdir -p "$state_dir" || {
            printf 'CRITICAL ERROR: Could not create state directory %s\n' "$state_dir" >&2
            exit 1
        }
    fi
}

parse_install_entry() {
    local entry="${1-}"
    local -n _mode_ref="$2"
    local -n _script_ref="$3"
    local -n _argv_ref="$4"
    local -n _base_state_key_ref="$5"
    local -n _ignore_fail_ref="$6"
    local -a _fields=()
    local -a _parts=()
    local _parsed_mode=""
    local _flags_part=""
    local _command_part=""

    IFS='|' read -r -a _fields <<< "$entry"
    case "${#_fields[@]}" in
        2)
            _parsed_mode="$(trim "${_fields[0]}")"
            _flags_part=""
            _command_part="$(trim "${_fields[1]}")"
            ;;
        3)
            _parsed_mode="$(trim "${_fields[0]}")"
            _flags_part="$(trim "${_fields[1]}")"
            _command_part="$(trim "${_fields[2]}")"
            ;;
        *)
            printf 'CRITICAL ERROR: Malformed INSTALL_SEQUENCE entry: %s\n' "$entry" >&2
            exit 1
            ;;
    esac

    if [[ "$_parsed_mode" != "U" && "$_parsed_mode" != "S" ]]; then
        printf 'CRITICAL ERROR: Invalid mode in INSTALL_SEQUENCE entry: %s\n' "$entry" >&2
        exit 1
    fi

    _ignore_fail_ref=0
    if [[ -n "$_flags_part" ]]; then
        local -a flag_tokens=()
        read -r -a flag_tokens <<< "${_flags_part//,/ }"
        local flag=""
        for flag in "${flag_tokens[@]}"; do
            case "$flag" in
                true|ignore|ignore-fail)
                    _ignore_fail_ref=1
                    ;;
                "") ;;
                *)
                    printf 'CRITICAL ERROR: Unsupported flag in INSTALL_SEQUENCE entry: %s\n' "$flag" >&2
                    exit 1
                    ;;
            esac
        done
    fi

    read -r -a _parts <<< "$_command_part"
    
    # Legacy backwards compatibility support for "true script.sh"
    if (( ${#_parts[@]} > 0 )) && [[ "${_parts[0]}" == "true" ]]; then
        _ignore_fail_ref=1
        _parts=("${_parts[@]:1}")
    fi

    if (( ${#_parts[@]} == 0 )); then
        printf 'CRITICAL ERROR: Missing script in INSTALL_SEQUENCE entry: %s\n' "$entry" >&2
        exit 1
    fi

    case "$_command_part" in
        *\'*|*\"*|*\\*)
            printf 'CRITICAL ERROR: INSTALL_SEQUENCE command field does not support quotes or backslash escapes: %s\n' "$entry" >&2
            exit 1
            ;;
    esac

    _mode_ref="$_parsed_mode"
    _script_ref="${_parts[0]}"
    _argv_ref=("${_parts[@]:1}")
    _base_state_key_ref="${_parsed_mode}|${_command_part}"
}

make_state_key() {
    local base_state_key="$1"
    local occurrence_index="$2"
    printf '%s|%d' "$base_state_key" "$occurrence_index"
}

state_is_completed() {
    local state_key="$1"
    [[ -n "${COMPLETED_SCRIPTS[$state_key]:-}" ]]
}

load_state() {
    unset COMPLETED_SCRIPTS
    declare -gA COMPLETED_SCRIPTS=()

    if [[ -s "$STATE_FILE" ]]; then
        local _state_lines=()
        local _line=""

        mapfile -t _state_lines < "$STATE_FILE" 2>/dev/null || true

        for _line in "${_state_lines[@]}"; do
            if [[ -n "$_line" ]]; then
                COMPLETED_SCRIPTS["$_line"]=1
            fi
        done
    fi
}

resolve_script() {
    local name="$1"
    local cached_path=""

    cached_path="${SCRIPT_CACHE[$name]:-}"
    if [[ -n "$cached_path" && -f "$cached_path" && -r "$cached_path" ]]; then
        printf '%s' "$cached_path"
        return 0
    fi

    unset 'SCRIPT_CACHE[$name]'

    if [[ "$name" == */* ]]; then
        if [[ -f "$name" && -r "$name" ]]; then
            SCRIPT_CACHE["$name"]="$name"
            printf '%s' "$name"
            return 0
        fi
        return 1
    fi

    local dir=""
    for dir in "${SCRIPT_SEARCH_DIRS[@]}"; do
        if [[ -f "${dir}/${name}" && -r "${dir}/${name}" ]]; then
            SCRIPT_CACHE["$name"]="${dir}/${name}"
            printf '%s' "${dir}/${name}"
            return 0
        fi
    done

    return 1
}

report_search_locations() {
    local name="$1"

    if [[ "$name" == */* ]]; then
        log "ERROR" "Direct path not found or unreadable: $name"
    else
        log "ERROR" "Script '$name' not found as a readable file in any search directory:"
        local dir=""
        for dir in "${SCRIPT_SEARCH_DIRS[@]}"; do
            log "ERROR" "  - ${dir}/"
        done
    fi
}

validate_search_dirs() {
    local needs_search_dirs=0
    local valid=0
    local entry=""
    local mode=""
    local filename=""
    local base_state_key=""
    local ignore_fail=0
    local dir=""
    local -a args=()

    for entry in "${INSTALL_SEQUENCE[@]}"; do
        [[ -n "${entry//[[:space:]]/}" ]] || continue
        parse_install_entry "$entry" mode filename args base_state_key ignore_fail
        if [[ "$filename" != */* ]]; then
            needs_search_dirs=1
            break
        fi
    done

    if (( needs_search_dirs == 0 )); then
        log "INFO" "No search-directory lookups are needed for this run."
        return 0
    fi

    if [[ ${#SCRIPT_SEARCH_DIRS[@]} -eq 0 ]]; then
        log "ERROR" "SCRIPT_SEARCH_DIRS is empty, but search-based entries are configured."
        exit 1
    fi

    for dir in "${SCRIPT_SEARCH_DIRS[@]}"; do
        if [[ -d "$dir" ]]; then
            log "INFO" "Search directory OK: $dir"
            (( ++valid ))
        else
            log "WARN" "Search directory not found: $dir"
        fi
    done

    if (( valid == 0 )); then
        log "ERROR" "None of the configured search directories exist, but search-based entries are configured."
        exit 1
    fi
}

get_script_description() {
    local filepath="$1"
    local desc

    desc="$(sed -n '2s/^#[[:space:]]*//p' "$filepath" 2>/dev/null)"
    if [[ -z "$desc" ]]; then
        desc="$(sed -n '3s/^#[[:space:]]*//p' "$filepath" 2>/dev/null)"
    fi

    printf '%s' "${desc:-No description available}"
}

preflight_check() {
    local missing=0
    local entry=""
    local mode=""
    local filename=""
    local base_state_key=""
    local ignore_fail=0
    local script_path=""
    local -a args=()

    log "INFO" "Performing pre-flight validation..."

    for entry in "${INSTALL_SEQUENCE[@]}"; do
        [[ -n "${entry//[[:space:]]/}" ]] || continue
        parse_install_entry "$entry" mode filename args base_state_key ignore_fail

        if ! script_path="$(resolve_script "$filename")"; then
            log "ERROR" "Missing or unreadable: ${filename}"
            (( ++missing ))
        fi
    done

    if (( missing > 0 )); then
        echo -e "${RED}CRITICAL:${RESET} $missing script(s) could not be found or read."
        read -r -p "Continue anyway? [y/N]: " _choice
        if [[ "${_choice,,}" != "y" ]]; then
            log "ERROR" "Aborting execution."
            exit 1
        fi
    else
        log "SUCCESS" "All sequence files verified and cached."
    fi
}

lock_holder_summary() {
    local lock_real=""
    local fd=""
    local pid=""
    local cmdline=""
    local summary=""
    local -A seen_pids=()

    lock_real="$(readlink -f -- "$LOCK_FILE" 2>/dev/null || printf '%s' "$LOCK_FILE")"

    for fd in /proc/[0-9]*/fd/*; do
        [[ -e "$fd" ]] || continue
        if [[ "$(readlink -f -- "$fd" 2>/dev/null || true)" != "$lock_real" ]]; then
            continue
        fi

        pid="${fd#/proc/}"
        pid="${pid%%/*}"

        [[ "$pid" == "$$" ]] && continue
        [[ -n "${seen_pids[$pid]:-}" ]] && continue
        seen_pids["$pid"]=1

        if [[ -r "/proc/${pid}/cmdline" ]]; then
            cmdline="$(tr '\0' ' ' < "/proc/${pid}/cmdline" 2>/dev/null || true)"
            cmdline="${cmdline% }"
        else
            cmdline=""
        fi

        [[ -n "$cmdline" ]] || cmdline="[pid ${pid}]"
        summary+="  - PID ${pid}: ${cmdline}"$'\n'
    done

    printf '%s' "${summary%$'\n'}"
}

acquire_lock() {
    local choice=""
    local holders=""

    exec 9>"$LOCK_FILE" || {
        echo -e "${RED}ERROR: Could not open lock file: $LOCK_FILE${RESET}"
        return 1
    }

    if flock -n 9; then
        return 0
    fi

    echo -e "${RED}ERROR: Another instance of this script appears to be running.${RESET}"

    holders="$(lock_holder_summary)"
    if [[ -n "$holders" ]]; then
        printf '%s\n' "$holders"
    else
        echo -e "${YELLOW}No live lock holder could be identified.${RESET}"
    fi

    if [[ ! -t 0 ]]; then
        return 1
    fi

    printf 'The lock itself can only be safely cleared by acquiring it, not by deleting the path.\n'
    read -r -p "If you are sure no other instance is still active, retry acquiring the lock now? [y/N]: " choice

    case "${choice,,}" in
        y|yes)
            if flock -w 2 9; then
                echo -e "${YELLOW}WARNING: Lock became available after user-confirmed retry.${RESET}"
                return 0
            fi
            echo -e "${RED}ERROR: Lock is still held by another process.${RESET}"
            return 1
            ;;
        *)
            return 1
            ;;
    esac
}

show_help() {
    cat << EOF
Arch Linux Master Orchestrator

Usage: $(basename "$0") [OPTIONS]

Options:
    --help, -h       Show this help message and exit
    --dry-run, -d    Preview execution plan without running anything
    --reset          Clear progress state and start fresh
    --manual, -m     Prompt to enable interactive mode (ask before each script)

Description:
    This script orchestrates the execution of multiple setup scripts
    for Arch Linux with Hyprland. It tracks completed scripts and
    can resume from where it left off if interrupted.

    Scripts are searched in the directories listed in SCRIPT_SEARCH_DIRS
    (first match wins). Entries with a / in the name are treated as
    direct paths.

    INSTALL_SEQUENCE command fields use whitespace-separated arguments only.
    Quotes, backslash escapes, and spaces inside filenames/arguments are
    not supported.

Examples:
    $(basename "$0")              # Normal run (Autonomous Mode)
    $(basename "$0") --manual     # Run with prompt for Interactive Mode
    $(basename "$0") --dry-run    # Preview what would be executed
    $(basename "$0") --reset      # Reset progress and start over
EOF
    exit 0
}

main() {
    if [[ $EUID -eq 0 ]]; then
        echo -e "${RED}CRITICAL ERROR: This script must NOT be run as root!${RESET}"
        echo "The script handles sudo privileges internally for specific steps."
        echo "Please run as a normal user: ./ORCHESTRA.sh"
        exit 1
    fi

    if (( $# > 1 )); then
        echo -e "${RED}ERROR: Too many arguments.${RESET}"
        echo "Use --help to see available options."
        exit 1
    fi

    # --- READ-ONLY ARGUMENT HANDLING ---
    case "${1:-}" in
        --help|-h)
            show_help
            ;;
        --dry-run|-d)
            load_state

            echo -e "\n${YELLOW}=== DRY RUN MODE ===${RESET}"
            echo -e "State file: ${BOLD}${STATE_FILE}${RESET}\n"

            echo "Search directories:"
            local dir=""
            for dir in "${SCRIPT_SEARCH_DIRS[@]}"; do
                if [[ -d "$dir" ]]; then
                    echo -e "  ${GREEN}✓${RESET} $dir"
                else
                    echo -e "  ${RED}✗${RESET} $dir ${RED}(not found)${RESET}"
                fi
            done
            echo ""

            echo "Execution plan:"
            echo ""

            local i=0
            local completed_count=0
            local missing_count=0
            local entry=""
            local mode=""
            local filename=""
            local base_state_key=""
            local ignore_fail=0
            local state_key=""
            local occurrence_index=0
            local status=""
            local mode_label=""
            local display_name=""
            local -a args=()
            local -A seen_state_keys=()

            for entry in "${INSTALL_SEQUENCE[@]}"; do
                [[ -n "${entry//[[:space:]]/}" ]] || continue
                (( ++i ))

                parse_install_entry "$entry" mode filename args base_state_key ignore_fail
                (( ++seen_state_keys["$base_state_key"] ))
                occurrence_index="${seen_state_keys["$base_state_key"]}"
                state_key="$(make_state_key "$base_state_key" "$occurrence_index")"

                mode_label="USER"
                [[ "$mode" == "S" ]] && mode_label="SUDO"
                [[ $ignore_fail -eq 1 ]] && mode_label="${mode_label},IGN"

                display_name="$filename"
                if (( ${#args[@]} > 0 )); then
                    display_name+=" ${args[*]}"
                fi

                if ! resolve_script "$filename" > /dev/null; then
                    status="${RED}[MISSING]${RESET}"
                    (( ++missing_count ))
                elif state_is_completed "$state_key"; then
                    status="${GREEN}[DONE]${RESET}"
                    (( ++completed_count ))
                else
                    status="${BLUE}[PENDING]${RESET}"
                fi

                printf "  %3d. [%s] %-45s %s\n" "$i" "$mode_label" "$display_name" "$status"
            done

            echo ""
            echo -e "${BOLD}Summary:${RESET}"
            echo -e "  Total scripts: $i"
            echo -e "  Completed: ${GREEN}${completed_count}${RESET}"
            echo -e "  Pending: ${BLUE}$((i - completed_count - missing_count))${RESET}"
            if [[ $missing_count -gt 0 ]]; then
                echo -e "  Missing: ${RED}${missing_count}${RESET}"
            fi
            echo ""
            echo "No changes were made."
            exit 0
            ;;
    esac

    # --- CONCURRENT EXECUTION GUARD ---
    if ! acquire_lock; then
        exit 1
    fi

    # --- MUTATING ARGUMENT HANDLING ---
    local force_manual_prompt=0

    case "${1:-}" in
        --reset)
            rm -f "$STATE_FILE"
            echo "State file reset. Starting fresh."
            ;;
        --manual|-m)
            force_manual_prompt=1
            ;;
        "")
            ;;
        *)
            echo -e "${RED}ERROR: Unknown option '${1}'${RESET}"
            echo "Use --help to see available options."
            exit 1
            ;;
    esac

    setup_logging
    validate_search_dirs
    preflight_check

    local start_ts=$SECONDS

    # Check for sudo requirement
    local needs_sudo=0
    local entry=""
    local mode=""
    local filename=""
    local base_state_key=""
    local ignore_fail=0
    local -a args=()

    for entry in "${INSTALL_SEQUENCE[@]}"; do
        [[ -n "${entry//[[:space:]]/}" ]] || continue
        parse_install_entry "$entry" mode filename args base_state_key ignore_fail
        if [[ "$mode" == "S" ]]; then
            needs_sudo=1
            break
        fi
    done

    if [[ $needs_sudo -eq 1 ]]; then
        init_sudo
    fi

    ensure_state_dir
    touch "$STATE_FILE"

    # --- EXECUTION MODE SELECTION ---
    local interactive_mode=0

    if [[ "$force_manual_prompt" -eq 1 ]]; then
        echo -e "\n${YELLOW}>>> EXECUTION MODE <<<${RESET}"
        read -r -p "Do you want to run interactively (prompt before every script)? [y/N]: " _mode_choice
        if [[ "${_mode_choice,,}" == "y" || "${_mode_choice,,}" == "yes" ]]; then
            interactive_mode=1
            log "INFO" "Interactive mode selected. You will be asked before each script."
        else
            log "INFO" "Autonomous mode selected. Running all scripts without confirmation."
        fi
    else
        log "INFO" "Autonomous mode selected. Running all scripts without confirmation."
    fi

    # --- SESSION RECOVERY ---
    load_state

    local total_scripts=0
    local completed_scripts=0
    local -A temp_seen_keys=()

    for entry in "${INSTALL_SEQUENCE[@]}"; do
        [[ -n "${entry//[[:space:]]/}" ]] || continue
        (( ++total_scripts ))

        local t_mode="" t_filename="" t_base_key="" t_ignore=0
        local -a t_args=()
        parse_install_entry "$entry" t_mode t_filename t_args t_base_key t_ignore

        (( ++temp_seen_keys["$t_base_key"] ))
        local t_occ="${temp_seen_keys["$t_base_key"]}"
        local t_state_key="$(make_state_key "$t_base_key" "$t_occ")"

        if state_is_completed "$t_state_key"; then
            (( ++completed_scripts ))
        fi
    done

    if [[ -s "$STATE_FILE" && $completed_scripts -gt 0 ]]; then
        if [[ $completed_scripts -eq $total_scripts ]]; then
            echo -e "\n${GREEN}>>> ALL SCRIPTS COMPLETED <<<${RESET}"
            log "INFO" "All $total_scripts scripts have already been successfully completed."
            read -r -p "Do you want to [S]tart over completely or [Q]uit? [s/Q]: " _done_choice
            if [[ "${_done_choice,,}" == "s" || "${_done_choice,,}" == "start" ]]; then
                rm -f "$STATE_FILE"
                touch "$STATE_FILE"
                load_state
                log "INFO" "State file reset. Starting fresh."
                completed_scripts=0
            else
                log "INFO" "Exiting. Everything is already up to date."
                exit 0
            fi
        else
            echo -e "\n${YELLOW}>>> PREVIOUS SESSION DETECTED <<<${RESET}"
            if [[ $interactive_mode -eq 1 ]]; then
                read -r -p "Do you want to [C]ontinue where you left off or [S]tart over? [C/s]: " _session_choice
                if [[ "${_session_choice,,}" == "s" || "${_session_choice,,}" == "start" ]]; then
                    rm -f "$STATE_FILE"
                    touch "$STATE_FILE"
                    load_state
                    log "INFO" "State file reset. Starting fresh."
                    completed_scripts=0
                else
                    log "INFO" "Continuing from previous session ($completed_scripts/$total_scripts completed)."
                fi
            else
                log "INFO" "Previous session detected. Autonomous mode will continue from existing state ($completed_scripts/$total_scripts completed)."
            fi
        fi
    fi

    local current_index=0
    log "INFO" "Processing ${total_scripts} scripts..."

    local -a SKIPPED_OR_FAILED=()
    local -A seen_state_keys=()

    EXECUTION_PHASE=1

    for entry in "${INSTALL_SEQUENCE[@]}"; do
        [[ -n "${entry//[[:space:]]/}" ]] || continue
        (( ++current_index ))

        local state_key=""
        local occurrence_index=0
        local script_path=""
        local display_name=""

        parse_install_entry "$entry" mode filename args base_state_key ignore_fail
        (( ++seen_state_keys["$base_state_key"] ))
        occurrence_index="${seen_state_keys["$base_state_key"]}"
        state_key="$(make_state_key "$base_state_key" "$occurrence_index")"

        display_name="$filename"
        if (( ${#args[@]} > 0 )); then
            display_name+=" ${args[*]}"
        fi

        while true; do
            if script_path="$(resolve_script "$filename")"; then
                break
            fi

            report_search_locations "$filename"
            echo -e "${YELLOW}Action Required:${RESET} File is missing."
            read -r -p "Do you want to [S]kip to next, [R]etry check, or [Q]uit? (s/r/q): " _choice

            case "${_choice,,}" in
                s|skip)
                    log "WARN" "Skipping $display_name (User Selection)"
                    SKIPPED_OR_FAILED+=("$display_name")
                    continue 2
                    ;;
                r|retry)
                    log "INFO" "Retrying check for $display_name..."
                    sleep 1
                    ;;
                *)
                    log "INFO" "Stopping execution. Place the script in one of the search directories and rerun."
                    exit 1
                    ;;
            esac
        done

        if state_is_completed "$state_key"; then
            log "WARN" "[${current_index}/${total_scripts}] Skipping $display_name (Already Completed)"
            continue
        fi

        if [[ $interactive_mode -eq 1 ]]; then
            local desc=""
            desc="$(get_script_description "$script_path")"

            echo -e "\n${YELLOW}>>> NEXT SCRIPT [${current_index}/${total_scripts}]:${RESET} $display_name ($mode)"
            echo -e "    ${BOLD}Description:${RESET} $desc"

            read -r -p "Do you want to [P]roceed, [S]kip, or [Q]uit? (p/s/q): " _user_confirm
            case "${_user_confirm,,}" in
                s|skip)
                    log "WARN" "Skipping $display_name (User Selection)"
                    SKIPPED_OR_FAILED+=("$display_name")
                    continue
                    ;;
                q|quit)
                    log "INFO" "User requested exit."
                    exit 0
                    ;;
            esac
        fi

        local auto_retry_limit=0
        local auto_retry_count=0

        if [[ $interactive_mode -eq 0 ]]; then
            auto_retry_limit=3
        fi

        while true; do
            local result=0

            if (( auto_retry_limit > 0 && auto_retry_count < auto_retry_limit )); then
                (( ++auto_retry_count ))
                log "RUN" "[${current_index}/${total_scripts}] Executing: ${display_name} (${mode}) [attempt ${auto_retry_count}/${auto_retry_limit}]"
            else
                log "RUN" "[${current_index}/${total_scripts}] Executing: ${display_name} (${mode})"
            fi

            if [[ "$mode" == "S" ]]; then
                ( exec 9>&-; cd "$(dirname "$script_path")" && sudo bash "$(basename "$script_path")" "${args[@]}" ) || result=$?
            elif [[ "$mode" == "U" ]]; then
                ( exec 9>&-; cd "$(dirname "$script_path")" && bash "$(basename "$script_path")" "${args[@]}" ) || result=$?
            else
                log "ERROR" "Invalid mode '$mode' in config. Use 'S' or 'U'."
                exit 1
            fi

            if [[ $result -eq 0 ]]; then
                printf '%s\n' "$state_key" >> "$STATE_FILE"
                COMPLETED_SCRIPTS["$state_key"]=1
                log "SUCCESS" "Finished $display_name"

                if [[ "$POST_SCRIPT_DELAY" != "0" ]]; then
                    sleep "$POST_SCRIPT_DELAY"
                fi

                break
            fi

            if [[ $ignore_fail -eq 1 ]]; then
                log "WARN" "Failed $display_name (Exit Code: $result) - ignored via ignore-fail flag"
                SKIPPED_OR_FAILED+=("$display_name (soft failed)")
                break
            fi

            log "ERROR" "Failed $display_name (Exit Code: $result)."

            if (( auto_retry_limit > 0 && auto_retry_count < auto_retry_limit )); then
                log "WARN" "Autonomous mode: retrying $display_name automatically (next attempt $((auto_retry_count + 1))/${auto_retry_limit})..."
                sleep 1
                continue
            fi

            auto_retry_limit=0

            echo -e "${YELLOW}Action Required:${RESET} Script execution failed."
            read -r -p "Do you want to [S]kip to next, [R]etry, or [Q]uit? (s/r/q): " _fail_choice

            case "${_fail_choice,,}" in
                s|skip)
                    log "WARN" "Skipping $display_name (User Selection). NOT marking as complete."
                    SKIPPED_OR_FAILED+=("$display_name")
                    break
                    ;;
                r|retry)
                    log "INFO" "Retrying $display_name..."
                    sleep 1
                    continue
                    ;;
                *)
                    log "INFO" "Stopping execution as requested."
                    exit 1
                    ;;
            esac
        done
    done

    # --- SUMMARY OF FAILED / SKIPPED SCRIPTS ---
    if [[ ${#SKIPPED_OR_FAILED[@]} -gt 0 ]]; then
        echo -e "\n${YELLOW}================================================================${RESET}"
        echo -e "${YELLOW}NOTE: Some scripts were skipped or failed:${RESET}"

        local f=""
        for f in "${SKIPPED_OR_FAILED[@]}"; do
            echo " - $f"
        done

        echo -e "\nYou can run them individually from their respective directories:"
        local dir=""
        for dir in "${SCRIPT_SEARCH_DIRS[@]}"; do
            if [[ -d "$dir" ]]; then
                echo -e "  ${BOLD}${dir}/${RESET}"
            fi
        done

        echo -e "${YELLOW}================================================================${RESET}\n"
    fi

    local end_ts=$SECONDS
    local duration=$((end_ts - start_ts))
    local minutes=$((duration / 60))
    local seconds=$((duration % 60))

    # --- COMPLETION & REBOOT NOTICE ---
    echo -e "\n${GREEN}================================================================${RESET}"
    echo -e "${BOLD}FINAL INSTRUCTIONS:${RESET}"
    echo -e "1. Execution Time: ${BOLD}${minutes}m ${seconds}s${RESET}"
    echo -e "2. Please ${BOLD}REBOOT YOUR SYSTEM${RESET} for all changes to take effect."
    echo -e "3. This script is designed to be run multiple times."
    echo -e "   If you think something wasn't done right, you can run this script again."
    echo -e "   It will ${BOLD}NOT${RESET} re-download the whole thing again, but instead"
    echo -e "   only download/configure what might have failed the first time."
    echo -e "${GREEN}================================================================${RESET}\n"
}

main "$@"
