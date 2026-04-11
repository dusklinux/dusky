#!/usr/bin/env bash
# ==============================================================================
#  UNIFIED ARCH ORCHESTRATOR (v3.7 - Session Aware Engine)
#  Context: Self-aware Phase 1 (ISO) and Phase 2 (Chroot) execution.
#  Usage: ./000_dusky_arch_install.sh [--auto|-a] [--manual|-m] [--dry-run|-d] [--reset] [--help|-h]
# ==============================================================================

# --- 1. SCRIPT SEQUENCES ---
# Soft-Failure Syntax: Append " | IGNORE" to allow the script to proceed 
# even if a non-critical component fails.

declare -ra ISO_SEQUENCE=(
  "020_environment_prep.sh --auto"
  "030_partitioning.sh --auto"
  "040_disk_mount.sh --auto"
  "050_mirrorlist.sh | IGNORE"
  "060_console_fix.sh"
  "070_pacstrap.sh --auto"
  "090_fstab.sh --auto"
)

declare -ra CHROOT_SEQUENCE=(
  "100_etc_skel.sh --auto"
  "110_post_chroot.sh --auto"
  "120_mkintcpip_optimizer.sh | IGNORE"
  "130_chroot_package_installer.sh --auto"
  "140_mkinitcpio_generation.sh"
  "150_limine_bootloader.sh --auto"
  "160_zram_config.sh"
  "170_services.sh"
  "180_exiting_unmounting.sh --auto"
)

# --- 2. SETUP & SAFETY ---
set -o errexit -o nounset -o pipefail -o errtrace

readonly SCRIPT_PATH="$(readlink -f "$0")"
readonly SCRIPT_DIR="$(dirname "$SCRIPT_PATH")"
readonly SCRIPT_NAME="$(basename "$SCRIPT_PATH")"
cd "$SCRIPT_DIR"

# Trap to ensure asynchronous log buffers flush cleanly on exit
cleanup() {
    exec 9>&- 2>/dev/null || true
    sleep 0.3
}
trap cleanup EXIT

# --- 3. ENVIRONMENT PASSTHROUGH (Cross-Chroot Bridge) ---
readonly ENV_PASSTHROUGH_FILE="$(pwd)/.env_passthrough"

if [[ -f "$ENV_PASSTHROUGH_FILE" ]]; then
    while IFS=$'\t' read -r key value_b64 || [[ -n "${key:-}" ]]; do
        [[ -n "${key:-}" ]] || continue
        case "$key" in
            AUTO_MODE|DRY_RUN|ROOT_PASS|USER_PASS|TARGET_HOSTNAME|TARGET_USER|TARGET_TZ)
                if [[ -n "${value_b64:-}" ]]; then
                    decoded_value="$(printf '%s' "$value_b64" | base64 --decode)" || {
                        printf '[ERR]   Invalid passthrough data for %s\n' "$key" >&2
                        exit 1
                    }
                else
                    decoded_value=""
                fi
                printf -v "$key" '%s' "$decoded_value"
                export "$key"
                ;;
        esac
    done < "$ENV_PASSTHROUGH_FILE"
fi

# --- 4. STATE, LOCKING & CHROOT AWARENESS ---
declare -a EXECUTED_SCRIPTS=() SKIPPED_SCRIPTS=() FAILED_SCRIPTS=() SOFT_FAILED_SCRIPTS=() INSTALL_SEQUENCE=()
declare -gA COMPLETED_SCRIPTS=()
declare -i DRY_RUN="${DRY_RUN:-0}" AUTO_MODE="${AUTO_MODE:-1}" IN_CHROOT=0 RESET_STATE=0 TOTAL_START_TIME

# Detect if we are running inside the arch-chroot via inode comparison
readonly ROOT_STAT="$(stat -c '%d:%i' / 2>/dev/null || true)"
readonly INIT_ROOT_STAT="$(stat -c '%d:%i' /proc/1/root/. 2>/dev/null || true)"

if [[ -n "$ROOT_STAT" && "$ROOT_STAT" != "$INIT_ROOT_STAT" ]]; then
    IN_CHROOT=1
    INSTALL_SEQUENCE=("${CHROOT_SEQUENCE[@]}")
    LOG_FILE="/var/log/arch-orchestrator-phase2-$(date +%Y%m%d-%H%M%S).log"
    STATE_FILE="/root/.arch_install_phase2.state"
    LOCK_FILE="/tmp/orchestrator_phase2.lock"
else
    INSTALL_SEQUENCE=("${ISO_SEQUENCE[@]}")
    LOG_FILE="/tmp/arch-orchestrator-phase1-$(date +%Y%m%d-%H%M%S).log"
    STATE_FILE="/tmp/.arch_install_phase1.state"
    LOCK_FILE="/tmp/orchestrator_phase1.lock"
fi

# ANSI-Stripped Logging via Process Substitution
exec > >(exec 9>&-; tee >(sed 's/\x1B\[[0-9;]*[a-zA-Z]//g' >> "$LOG_FILE")) 2>&1

# --- 5. VISUALS & LOGGING ---
if [[ -t 1 ]]; then
    readonly R=$'\e[31m' G=$'\e[32m' B=$'\e[34m' Y=$'\e[33m' HL=$'\e[1m' RS=$'\e[0m'
else
    readonly R="" G="" B="" Y="" HL="" RS=""
fi

log() {
    case "$1" in
        INFO) printf "%s[INFO]%s  %s\n" "$B" "$RS" "$2" ;;
        OK)   printf "%s[OK]%s    %s\n" "$G" "$RS" "$2" ;;
        WARN) printf "%s[WARN]%s  %s\n" "$Y" "$RS" "$2" >&2 ;;
        ERR)  printf "%s[ERR]%s   %s\n" "$R" "$RS" "$2" >&2 ;;
    esac
}

print_summary() {
    local end_ts=$SECONDS
    local duration=$((end_ts - TOTAL_START_TIME))

    printf "\n%s%s=== PHASE SUMMARY ===%s\n" "$B" "$HL" "$RS"
    
    if (( ${#EXECUTED_SCRIPTS[@]} > 0 )); then
        printf "%s[Executed]%s    %d script(s)\n" "$G" "$RS" "${#EXECUTED_SCRIPTS[@]}"
    fi
    
    if (( ${#SKIPPED_SCRIPTS[@]} > 0 )); then
        printf "%s[Skipped]%s     %d script(s):\n" "$Y" "$RS" "${#SKIPPED_SCRIPTS[@]}"
        for s in "${SKIPPED_SCRIPTS[@]}"; do printf "  - %s\n" "$s"; done
    fi

    if (( ${#SOFT_FAILED_SCRIPTS[@]} > 0 )); then
        printf "%s[Soft-Failed]%s %d script(s) (Ignored):\n" "$Y" "$RS" "${#SOFT_FAILED_SCRIPTS[@]}"
        for s in "${SOFT_FAILED_SCRIPTS[@]}"; do printf "  - %s\n" "$s"; done
    fi
    
    if (( ${#FAILED_SCRIPTS[@]} > 0 )); then
        printf "%s[Failed]%s      %d script(s):\n" "$R" "$RS" "${#FAILED_SCRIPTS[@]}"
        for s in "${FAILED_SCRIPTS[@]}"; do printf "  - %s\n" "$s"; done
    fi
    
    printf "\n%sExecution Time:%s %dm %ds\n" "$B" "$RS" $((duration / 60)) $((duration % 60))
    printf "%sLog file:%s  %s\n" "$B" "$RS" "$LOG_FILE"
}

# --- 6. HELPER FUNCTIONS ---
show_help() {
    printf "\n%s=== UNIFIED ARCH ORCHESTRATOR HELP ===%s\n\n" "$B" "$RS"
    printf "Usage: %s./%s [OPTIONS]%s\n\n" "$HL" "$SCRIPT_NAME" "$RS"
    printf "Options:\n"
    printf "  %s-a, --auto%s      Run autonomously without prompting (Default)\n" "$G" "$RS"
    printf "  %s-m, --manual%s    Prompt whether to run interactively (step-by-step)\n" "$Y" "$RS"
    printf "  %s-d, --dry-run%s   Simulate execution without running scripts\n" "$B" "$RS"
    printf "  %s--reset%s         Reset the state file and start fresh\n" "$R" "$RS"
    printf "  %s-h, --help%s      Display this help and exit\n\n" "$HL" "$RS"
}

load_state() {
    unset COMPLETED_SCRIPTS
    declare -gA COMPLETED_SCRIPTS=()
    if [[ -s "$STATE_FILE" ]]; then
        local _state_lines=()
        mapfile -t _state_lines < "$STATE_FILE" 2>/dev/null || true
        for _line in "${_state_lines[@]}"; do
            [[ -n "$_line" ]] && COMPLETED_SCRIPTS["$_line"]=1
        done
    fi
}

get_script_description() {
    local filepath="$1"
    local desc
    desc=$(sed -n '2s/^#[[:space:]]*//p' "$filepath" 2>/dev/null)
    if [[ -z "$desc" ]]; then
        desc=$(sed -n '3s/^#[[:space:]]*//p' "$filepath" 2>/dev/null)
    fi
    printf "%s" "${desc:-No description available}"
}

# --- 7. EXECUTION ENGINE ---
execute_script() {
    local entry="$1" state_key="$2" current="$3" total="$4" start_time exit_code
    
    # Parse Command vs Failure Mode (IGNORE syntax)
    local raw_command fail_mode ignore_fail=0
    IFS='|' read -r raw_command fail_mode <<< "$entry"
    raw_command="${raw_command#"${raw_command%%[![:space:]]*}"}" # Trim leading
    raw_command="${raw_command%"${raw_command##*[![:space:]]}"}" # Trim trailing

    if [[ -n "$fail_mode" && "${fail_mode,,}" == *"ignore"* ]]; then
        ignore_fail=1
    fi

    # Extract script name and args
    local script_name script_args
    read -r script_name script_args <<< "$raw_command"

    if [[ -n "${COMPLETED_SCRIPTS[$state_key]:-}" ]]; then
        log OK "[$current/$total] Skipping: ${HL}$script_name${RS} (Already Completed)"
        return 0
    fi

    # Propagate Orchestrator arguments downward
    local child_args=()
    [[ -n "$script_args" ]] && read -ra appended_args <<< "$script_args" && child_args+=("${appended_args[@]}")
    (( DRY_RUN )) && child_args+=("--dry-run")

    # Retry Engine Mechanics
    local max_attempts=3
    local attempt=1

    while true; do
        if (( attempt > 1 )); then
            log INFO "[$current/$total] Retrying: ${HL}$raw_command${RS} (Attempt $attempt/$max_attempts)"
        else
            log INFO "[$current/$total] Executing: ${HL}$raw_command${RS}"
        fi
        
        start_time=$SECONDS

        set +e
        bash "$script_name" "${child_args[@]}"
        exit_code=$?
        set -e

        if (( exit_code == 0 )); then
            echo "$state_key" >> "$STATE_FILE"
            COMPLETED_SCRIPTS["$state_key"]=1
            log OK "Finished: $script_name ($((SECONDS - start_time))s)"
            EXECUTED_SCRIPTS+=("$script_name")
            return 0
        fi

        # Handle Soft Failures (Happens BEFORE the retry mechanism)
        if (( ignore_fail == 1 )); then
            log WARN "Failed: $script_name (Exit Code: $exit_code) - ignored via IGNORE flag."
            SOFT_FAILED_SCRIPTS+=("$script_name")
            return 0
        fi

        # Evaluate Retries (Network Resilience)
        if (( attempt < max_attempts )); then
            ((attempt++))
            log WARN "Execution failed (Exit Code: $exit_code). Waiting 2 seconds before retry..."
            sleep 2
            continue
        fi

        # Absolute Failure Reached
        log ERR "Failed: $script_name (Exit Code: $exit_code)"
        FAILED_SCRIPTS+=("$script_name")

        if (( AUTO_MODE )); then
            log ERR "AUTO_MODE is enabled and retry limit reached; aborting."
            exit "$exit_code"
        fi

        printf "%sAction Required:%s Script execution failed after %d attempts.\n" "$Y" "$RS" "$max_attempts"
        if ! read -r -p "[R]etry, [S]kip, or [A]bort? (r/s/a): " action; then
            log ERR "Interactive input closed; aborting."
            exit "$exit_code"
        fi
        
        case "${action,,}" in
            r|retry)
                unset 'FAILED_SCRIPTS[-1]'
                attempt=1 # Reset retry counter on manual operator intervention
                continue
                ;;
            s|skip)
                log WARN "Skipping. NOT marking as complete."
                unset 'FAILED_SCRIPTS[-1]'
                SKIPPED_SCRIPTS+=("$script_name")
                return 0
                ;;
            *)
                exit "$exit_code"
                ;;
        esac
    done
}

# --- 8. MAIN FUNCTION ---
main() {
    TOTAL_START_TIME=$SECONDS

    for arg in "$@"; do
        case "$arg" in
            -a|--auto) AUTO_MODE=1 ;;
            -m|--manual) AUTO_MODE=0 ;;
            -d|--dry-run) DRY_RUN=1 ;;
            --reset) RESET_STATE=1 ;;
            -h|--help)
                show_help
                exit 0
                ;;
            *)
                log ERR "Unknown argument: $arg"
                log INFO "Use --help to see available options."
                exit 2
                ;;
        esac
    done

    # Base Safety Checks
    if (( EUID != 0 )); then
        log ERR "This orchestrator must be run as root."
        exit 1
    fi

    if (( AUTO_MODE == 0 && DRY_RUN == 0 )) && [[ ! -t 0 ]]; then
        log ERR "Interactive mode requires a TTY on stdin. Re-run from an interactive terminal or use --auto."
        exit 1
    fi

    # Concurrency Guard & Forensic Lock Diagnostics
    exec 9>"$LOCK_FILE"
    if ! flock -n 9; then
        log ERR "Another instance of this orchestrator is already running."
        
        local lock_real holders="" fd pid cmdline
        lock_real="$(readlink -f -- "$LOCK_FILE" 2>/dev/null || printf '%s' "$LOCK_FILE")"
        
        for fd in /proc/[0-9]*/fd/*; do
            [[ -e "$fd" ]] || continue
            if [[ "$(readlink -f -- "$fd" 2>/dev/null || true)" == "$lock_real" ]]; then
                pid="${fd#/proc/}"; pid="${pid%%/*}"
                [[ "$pid" == "$$" ]] && continue # Ignore self
                cmdline="$(tr '\0' ' ' < "/proc/${pid}/cmdline" 2>/dev/null || true)"
                holders+="  - PID ${pid}: ${cmdline}\n"
            fi
        done
        
        if [[ -n "$holders" ]]; then
            printf "\n%sLock is held by the following process(es):%s\n%b\n" "$Y" "$RS" "$holders"
            printf "You can manually terminate it using: %skill -9 <PID>%s\n" "$HL" "$RS"
        fi
        
        exit 1
    fi

    # State Reset & Loading
    if (( RESET_STATE )); then
        rm -f "$STATE_FILE"
        log INFO "State file reset (--reset flag passed). Starting fresh."
    fi
    touch "$STATE_FILE"
    load_state

    # --- THE DRY-RUN TABLE PLANNER ---
    if (( DRY_RUN )); then
        printf "\n%s%s=== DRY RUN EXECUTION PLAN ===%s\n\n" "$Y" "$HL" "$RS"
        printf "State file: %s\n\n" "$STATE_FILE"
        
        local i=0 completed_count=0 missing_count=0 status
        declare -A dryrun_seen_keys=()
        
        for entry in "${INSTALL_SEQUENCE[@]}"; do
            ((++i))
            local raw_command fail_mode ignore_fail=0
            IFS='|' read -r raw_command fail_mode <<< "$entry"
            raw_command="${raw_command#"${raw_command%%[![:space:]]*}"}"
            
            if [[ -n "$fail_mode" && "${fail_mode,,}" == *"ignore"* ]]; then
                ignore_fail=1
            fi
            
            local script_name
            read -r script_name _ <<< "$raw_command"
            
            # Key Generation for idempotency
            (( ++dryrun_seen_keys["$script_name"] ))
            local state_key="${script_name}|${dryrun_seen_keys["$script_name"]}"
            
            if [[ ! -f "$script_name" ]]; then
                status="${R}[MISSING]${RS}"
                ((++missing_count))
            elif [[ -n "${COMPLETED_SCRIPTS[$state_key]:-}" ]]; then
                status="${G}[DONE]${RS}"
                ((++completed_count))
            else
                status="${B}[PENDING]${RS}"
            fi
            
            local display_name="$raw_command"
            (( ignore_fail == 1 )) && display_name+=" (IGN)"
            printf "  %3d. %-45s %s\n" "$i" "$display_name" "$status"
        done
        
        printf "\n%sSummary:%s\n" "$HL" "$RS"
        printf "  Total scripts: %d\n" "$i"
        printf "  Completed:   %s%d%s\n" "$G" "$completed_count" "$RS"
        printf "  Pending:     %s%d%s\n" "$B" $((i - completed_count - missing_count)) "$RS"
        (( missing_count > 0 )) && printf "  Missing:     %s%d%s\n" "$R" "$missing_count" "$RS"
        printf "\nNo changes were made. Exiting.\n"
        exit 0
    fi

    if (( IN_CHROOT )); then
        printf "\n%s%s=== ARCH ORCHESTRATOR (PHASE 2: CHROOT) ===%s\n\n" "$B" "$HL" "$RS"
    else
        printf "\n%s%s=== ARCH ORCHESTRATOR (PHASE 1: ISO) ===%s\n\n" "$B" "$HL" "$RS"
    fi

    # --- SESSION RECOVERY ---
    local total_scripts=${#INSTALL_SEQUENCE[@]}
    local completed_scripts=0
    declare -A temp_seen_keys=()
    
    # Safe string assignment for the phase label
    local phase_label="PHASE 1"
    (( IN_CHROOT )) && phase_label="PHASE 2"

    for entry in "${INSTALL_SEQUENCE[@]}"; do
        local raw_command script_name
        IFS='|' read -r raw_command _ <<< "$entry"
        raw_command="${raw_command#"${raw_command%%[![:space:]]*}"}"
        read -r script_name _ <<< "$raw_command"
        
        (( ++temp_seen_keys["$script_name"] ))
        local t_state_key="${script_name}|${temp_seen_keys["$script_name"]}"
        
        if [[ -n "${COMPLETED_SCRIPTS[$t_state_key]:-}" ]]; then
            (( ++completed_scripts ))
        fi
    done

    if [[ -s "$STATE_FILE" && $completed_scripts -gt 0 ]]; then
        if [[ $completed_scripts -eq $total_scripts ]]; then
            printf "%s>>> ALL %s SCRIPTS COMPLETED <<<%s\n" "$G" "$phase_label" "$RS"
            if (( AUTO_MODE == 0 )); then
                if (( ! IN_CHROOT )); then
                    printf "Phase 1 (ISO) is already fully completed.\n"
                    read -r -p "Do you want to [C]ontinue to Phase 2, [S]tart Phase 1 over, or [Q]uit? [C/s/q]: " _done_choice
                    case "${_done_choice,,}" in
                        s|start)
                            rm -f "$STATE_FILE"
                            load_state
                            completed_scripts=0
                            log INFO "Phase 1 state reset. Starting fresh."
                            ;;
                        q|quit)
                            log INFO "Exiting as requested."
                            exit 0
                            ;;
                        *)
                            log INFO "Continuing to Phase 2 boundary crossing..."
                            ;;
                    esac
                else
                    printf "Phase 2 (Chroot) is already fully completed.\n"
                    read -r -p "Do you want to [S]tart Phase 2 over or [Q]uit and finalize? [s/Q]: " _done_choice
                    if [[ "${_done_choice,,}" == "s" || "${_done_choice,,}" == "start" ]]; then
                        rm -f "$STATE_FILE"
                        load_state
                        completed_scripts=0
                        log INFO "Phase 2 state reset. Starting fresh."
                    else
                        log INFO "Finalizing Phase 2. Exiting chroot."
                    fi
                fi
            else
                log INFO "Autonomous mode: Proceeding through completed scripts..."
            fi
            printf "\n"
        else
            printf "%s>>> PREVIOUS %s SESSION DETECTED <<<%s\n" "$Y" "$phase_label" "$RS"
            if (( AUTO_MODE == 0 )); then
                read -r -p "Do you want to [C]ontinue where you left off or [S]tart over? [C/s]: " _session_choice
                if [[ "${_session_choice,,}" == "s" || "${_session_choice,,}" == "start" ]]; then
                    rm -f "$STATE_FILE"
                    load_state
                    completed_scripts=0
                    log INFO "State reset. Starting fresh."
                else
                    log INFO "Continuing from previous state ($completed_scripts/$total_scripts completed)."
                fi
            else
                log INFO "Autonomous mode: Continuing from existing state ($completed_scripts/$total_scripts completed)."
            fi
            printf "\n"
        fi
    fi

    # --- EXECUTION MODE SELECTION ---
    if (( AUTO_MODE == 0 && DRY_RUN == 0 && IN_CHROOT == 0 )); then
        printf "%s>>> EXECUTION MODE <<<%s\n" "$Y" "$RS"
        read -r -p "Do you want to run interactively (prompt before every script)? [y/N]: " _mode_choice
        if [[ "${_mode_choice,,}" != "y" && "${_mode_choice,,}" != "yes" ]]; then
            AUTO_MODE=1
            log INFO "Autonomous mode selected. Running all scripts without confirmation."
        else
            log INFO "Interactive mode selected. You will be asked before each script."
        fi
        printf "\n"
    fi

    # --- COMPREHENSIVE PRE-FLIGHT AUDIT ---
    local missing_audit_count=0
    for entry in "${INSTALL_SEQUENCE[@]}"; do
        local raw_command script_name
        IFS='|' read -r raw_command _ <<< "$entry"
        raw_command="${raw_command#"${raw_command%%[![:space:]]*}"}"
        read -r script_name _ <<< "$raw_command"

        if [[ ! -f "$script_name" ]]; then
            log ERR "Pre-flight check failed: Missing script '$script_name'"
            ((missing_audit_count++))
        fi
    done

    if (( missing_audit_count > 0 )); then
        log ERR "Pre-flight audit failed ($missing_audit_count script(s) missing). Aborting to prevent incomplete execution."
        exit 1
    else
        log OK "Pre-flight audit passed. All sequence payloads verified."
    fi

    # --- EXECUTION LOOP ---
    local current=0 total=${#INSTALL_SEQUENCE[@]}
    declare -A exec_seen_keys=()

    for entry in "${INSTALL_SEQUENCE[@]}"; do
        ((++current))

        # Re-parse purely to get naming for interactive prompt and unique Key
        local raw_command script_name
        IFS='|' read -r raw_command _ <<< "$entry"
        raw_command="${raw_command#"${raw_command%%[![:space:]]*}"}"
        read -r script_name _ <<< "$raw_command"
        
        # Generator for Multi-Occurrence State Keys
        (( ++exec_seen_keys["$script_name"] ))
        local state_key="${script_name}|${exec_seen_keys["$script_name"]}"

        # Only prompt if NOT completed AND NOT in auto mode
        if [[ -z "${COMPLETED_SCRIPTS[$state_key]:-}" ]] && (( AUTO_MODE == 0 )); then
            local desc
            desc=$(get_script_description "$script_name")
            
            printf "\n%s>>> NEXT [%d/%d]:%s %s\n" "$Y" "$current" "$total" "$RS" "$raw_command"
            printf "    %sDescription:%s %s\n" "$B" "$RS" "$desc"
            
            if ! read -r -p "Proceed? [P]roceed, [S]kip, [Q]uit: " confirm; then
                log ERR "Interactive input closed; aborting."
                print_summary
                exit 1
            fi
            
            case "${confirm,,}" in
                s*)
                    SKIPPED_SCRIPTS+=("$script_name")
                    continue
                    ;;
                q*)
                    print_summary
                    exit 0
                    ;;
            esac
        fi
        
        # Fire Engine
        execute_script "$entry" "$state_key" "$current" "$total"
    done

    print_summary

    # --- PHASE TRANSITION BRIDGE (Executes only if Phase 1 succeeded cleanly) ---
    if (( ! IN_CHROOT )); then
        if (( ${#FAILED_SCRIPTS[@]} > 0 || ${#SKIPPED_SCRIPTS[@]} > 0 )); then
            log WARN "Phase 1 did not complete fully; not initiating Phase 2 boundary crossing."
            return 0
        fi

        printf "\n%s%s=== BASE SYSTEM INSTALLED - INITIATING PHASE 2 ===%s\n" "$G" "$HL" "$RS"

        local CHROOT_MNT="/mnt"
        local TMP_DIR="/root/arch_install_tmp"
        local TARGET_TMP="${CHROOT_MNT}${TMP_DIR}"
        local finish_flag="${CHROOT_MNT}/root/.arch-installer-finish-auto"

        log INFO "Clearing any stale autonomous-finish sentinel..."
        rm -f "$finish_flag"

        log INFO "Cloning orchestrator payload to Phase 2 environment..."
        mkdir -p "$TARGET_TMP"
        
        # Safely copy all files including hidden dotfiles
        shopt -s dotglob
        cp -a ./* "${TARGET_TMP}/"
        shopt -u dotglob

        log INFO "Securing environment state for boundary crossing..."
        install -m 600 /dev/null "${TARGET_TMP}/.env_passthrough"
        {
            printf 'AUTO_MODE\t%s\n' "$(printf '%s' "$AUTO_MODE" | base64 --wrap=0)"
            printf 'DRY_RUN\t%s\n' "$(printf '%s' "$DRY_RUN" | base64 --wrap=0)"
            printf 'ROOT_PASS\t%s\n' "$(printf '%s' "${ROOT_PASS:-}" | base64 --wrap=0)"
            printf 'USER_PASS\t%s\n' "$(printf '%s' "${USER_PASS:-}" | base64 --wrap=0)"
            printf 'TARGET_HOSTNAME\t%s\n' "$(printf '%s' "${TARGET_HOSTNAME:-}" | base64 --wrap=0)"
            printf 'TARGET_USER\t%s\n' "$(printf '%s' "${TARGET_USER:-}" | base64 --wrap=0)"
            printf 'TARGET_TZ\t%s\n' "$(printf '%s' "${TARGET_TZ:-}" | base64 --wrap=0)"
        } > "${TARGET_TMP}/.env_passthrough"

        log INFO "Handing control to arch-chroot..."

        local -a phase2_args=()
        (( AUTO_MODE )) && phase2_args+=(--auto)
        (( RESET_STATE )) && phase2_args+=(--reset)

        # Release the lock BEFORE crossing the boundary to prevent FD inheritance deadlocks
        exec 9>&-

        set +e
        arch-chroot "$CHROOT_MNT" /bin/bash "${TMP_DIR}/${SCRIPT_NAME}" "${phase2_args[@]}"
        local chroot_exit=$?
        set -e

        log INFO "Phase 2 execution terminated (Exit Code: $chroot_exit)."
        log INFO "Scrubbing temporary payload and sensitive environment data..."
        rm -rf "$TARGET_TMP"

        if (( chroot_exit != 0 )); then
            log ERR "Phase 2 encountered a fatal error."
            return "$chroot_exit"
        fi

        printf "\n%s%s=== COMPLETE SYSTEM DEPLOYMENT SUCCESSFUL ===%s\n" "$G" "$HL" "$RS"

        if [[ -f "$finish_flag" ]]; then
            rm -f "$finish_flag"
            log OK "Autonomous finish flag detected from 180_exiting_unmounting.sh."
            
            # --- THE FIX: Deactivate swap before unmounting ---
            log INFO "Deactivating swap to release kernel filesystem locks..."
            swapoff -a 2>/dev/null || true
            
            log INFO "Unmounting filesystems securely..."
            umount -R "$CHROOT_MNT"
            log OK "All filesystems flushed and unmounted."
            
            # --- NEW: Ask before powering off ---
            printf "\n"
            local _poweroff_choice="y"
            if [[ -t 0 ]]; then
                read -r -p ">>> System is completely unmounted. Power off now? [Y/n]: " _poweroff_choice || _poweroff_choice="y"
            fi
            
            if [[ "${_poweroff_choice,,}" != "n" && "${_poweroff_choice,,}" != "no" ]]; then
                printf "\n%s>>> POWERING OFF. PULL YOUR USB DRIVE WHEN SCREEN GOES BLACK. <<<%s\n" "$Y" "$RS"
                sleep 2
                poweroff
            else
                log INFO "Power off aborted. You are now back in the Live ISO environment."
            fi
        else
            if (( AUTO_MODE )); then
                log INFO "AUTO_MODE is enabled; skipping interactive shell prompt."
            else
                if ! read -r -p "Do you want to open an interactive shell in the new system? [y/N]: " shell_choice; then
                    shell_choice=""
                fi
                if [[ "${shell_choice,,}" == "y" ]]; then
                    arch-chroot "$CHROOT_MNT"
                fi
            fi
        fi
    fi
}

main "$@"
