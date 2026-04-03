#!/usr/bin/env bash
# ==============================================================================
#  UNIFIED ARCH ORCHESTRATOR (v3.3 - The Ultimate Edition)
#  Context: Self-aware Phase 1 (ISO) and Phase 2 (Chroot) execution.
#  Usage: ./000_dusky_arch_install.sh [--auto|-a] [--dry-run|-d] [--reset]
# ==============================================================================

# --- 1. SCRIPT SEQUENCES ---
declare -ra ISO_SEQUENCE=(
  "020_environment_prep.sh"
  "030_partitioning.sh"
  "040_disk_mount.sh --auto"
  "050_mirrorlist.sh"
  "060_console_fix.sh"
  "070_pacstrap.sh"
#  "080_script_directories_population_in_chroot.sh"
  "090_fstab.sh"
)

declare -ra CHROOT_SEQUENCE=(
  "100_etc_skel.sh"
  "110_post_chroot.sh"
  "120_mkintcpip_optimizer.sh"
  "130_chroot_package_installer.sh --auto"
  "140_mkinitcpio_generation.sh"
  "150_limine_bootloader.sh"
  "160_zram_config.sh"
  "170_services.sh"
  "180_exiting_unmounting.sh"
)

# --- 2. SETUP & SAFETY ---
set -o errexit -o nounset -o pipefail -o errtrace

readonly SCRIPT_PATH="$(readlink -f "$0")"
readonly SCRIPT_DIR="$(dirname "$SCRIPT_PATH")"
readonly SCRIPT_NAME="$(basename "$SCRIPT_PATH")"
cd "$SCRIPT_DIR"

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
declare -a EXECUTED_SCRIPTS=() SKIPPED_SCRIPTS=() FAILED_SCRIPTS=() INSTALL_SEQUENCE=()
declare -gA COMPLETED_SCRIPTS=()
declare -i DRY_RUN="${DRY_RUN:-0}" AUTO_MODE="${AUTO_MODE:-0}" IN_CHROOT=0 RESET_STATE=0 TOTAL_START_TIME

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
        printf "%s[Executed]%s %d script(s)\n" "$G" "$RS" "${#EXECUTED_SCRIPTS[@]}"
    fi
    
    if (( ${#SKIPPED_SCRIPTS[@]} > 0 )); then
        printf "%s[Skipped]%s  %d script(s):\n" "$Y" "$RS" "${#SKIPPED_SCRIPTS[@]}"
        for s in "${SKIPPED_SCRIPTS[@]}"; do printf "  - %s\n" "$s"; done
    fi
    
    if (( ${#FAILED_SCRIPTS[@]} > 0 )); then
        printf "%s[Failed]%s   %d script(s):\n" "$R" "$RS" "${#FAILED_SCRIPTS[@]}"
        for s in "${FAILED_SCRIPTS[@]}"; do printf "  - %s\n" "$s"; done
    fi
    
    printf "\n%sExecution Time:%s %dm %ds\n" "$B" "$RS" $((duration / 60)) $((duration % 60))
    printf "%sLog file:%s  %s\n" "$B" "$RS" "$LOG_FILE"
}

# --- 6. HELPER FUNCTIONS ---
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
    local entry="$1" current="$2" total="$3" start_time exit_code
    
    # Split filename from arguments
    local script_name script_args
    read -r script_name script_args <<< "$entry"

    if [[ -n "${COMPLETED_SCRIPTS[$script_name]:-}" ]]; then
        log OK "[$current/$total] Skipping: ${HL}$script_name${RS} (Already Completed)"
        return 0
    fi

    # Propagate Orchestrator arguments downward to child scripts
    local child_args=()
    [[ -n "$script_args" ]] && read -ra appended_args <<< "$script_args" && child_args+=("${appended_args[@]}")
    (( AUTO_MODE )) && child_args+=("--auto")
    (( DRY_RUN )) && child_args+=("--dry-run")

    while true; do
        log INFO "[$current/$total] Executing: ${HL}$entry${RS}"
        start_time=$SECONDS

        set +e
        bash "$script_name" "${child_args[@]}"
        exit_code=$?
        set -e

        if (( exit_code == 0 )); then
            echo "$script_name" >> "$STATE_FILE"
            COMPLETED_SCRIPTS["$script_name"]=1
            log OK "Finished: $script_name ($((SECONDS - start_time))s)"
            EXECUTED_SCRIPTS+=("$script_name")
            return 0
        else
            log ERR "Failed: $script_name (Exit Code: $exit_code)"
            FAILED_SCRIPTS+=("$script_name")

            if (( AUTO_MODE )); then
                log ERR "AUTO_MODE is enabled; aborting after failure."
                exit "$exit_code"
            fi

            printf "%sAction Required:%s Script execution failed.\n" "$Y" "$RS"
            if ! read -r -p "[R]etry, [S]kip, or [A]bort? (r/s/a): " action; then
                log ERR "Interactive input closed; aborting."
                exit "$exit_code"
            fi
            
            case "${action,,}" in
                r|retry)
                    unset 'FAILED_SCRIPTS[-1]'
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
        fi
    done
}

# --- 8. MAIN FUNCTION ---
main() {
    TOTAL_START_TIME=$SECONDS

    for arg in "$@"; do
        case "$arg" in
            -a|--auto) AUTO_MODE=1 ;;
            -d|--dry-run) DRY_RUN=1 ;;
            --reset) RESET_STATE=1 ;;
            *)
                log ERR "Unknown argument: $arg"
                exit 2
                ;;
        esac
    done

    # Pre-flight Checks
    if (( EUID != 0 )); then
        log ERR "This orchestrator must be run as root."
        exit 1
    fi

    if (( AUTO_MODE == 0 && DRY_RUN == 0 )) && [[ ! -t 0 ]]; then
        log ERR "Interactive mode requires a TTY on stdin. Re-run from an interactive terminal or use --auto."
        exit 1
    fi

    # Concurrency Guard
    exec 9>"$LOCK_FILE"
    if ! flock -n 9; then
        log ERR "Another instance of this orchestrator is already running."
        exit 1
    fi

    # State Reset & Loading
    if (( RESET_STATE )); then
        rm -f "$STATE_FILE"
        log INFO "State file reset. Starting fresh."
    fi
    touch "$STATE_FILE"
    load_state

    # --- THE DRY-RUN TABLE PLANNER ---
    if (( DRY_RUN )); then
        printf "\n%s%s=== DRY RUN EXECUTION PLAN ===%s\n\n" "$Y" "$HL" "$RS"
        printf "State file: %s\n\n" "$STATE_FILE"
        
        local i=0 completed_count=0 missing_count=0 status
        for entry in "${INSTALL_SEQUENCE[@]}"; do
            ((++i))
            read -r script_name _ <<< "$entry"
            
            if [[ ! -f "$script_name" ]]; then
                status="${R}[MISSING]${RS}"
                ((++missing_count))
            elif [[ -n "${COMPLETED_SCRIPTS[$script_name]:-}" ]]; then
                status="${G}[DONE]${RS}"
                ((++completed_count))
            else
                status="${B}[PENDING]${RS}"
            fi
            printf "  %3d. %-45s %s\n" "$i" "$entry" "$status"
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

    # Script Existence Validation
    for entry in "${INSTALL_SEQUENCE[@]}"; do
        read -r script_name _ <<< "$entry"
        if [[ ! -f "$script_name" ]]; then
            log ERR "Missing script: $script_name"
            exit 1
        fi
    done

    # --- EXECUTION LOOP ---
    local current=0 total=${#INSTALL_SEQUENCE[@]}
    for entry in "${INSTALL_SEQUENCE[@]}"; do
        ((++current))
        read -r script_name _ <<< "$entry"

        # Only prompt if NOT completed AND NOT in auto mode
        if [[ -z "${COMPLETED_SCRIPTS[$script_name]:-}" ]] && (( AUTO_MODE == 0 )); then
            local desc
            desc=$(get_script_description "$script_name")
            
            printf "\n%s>>> NEXT [%d/%d]:%s %s\n" "$Y" "$current" "$total" "$RS" "$entry"
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
        execute_script "$entry" "$current" "$total"
    done

    print_summary

    # --- PHASE TRANSITION BRIDGE (Executes only if Phase 1 succeeded cleanly) ---
    if (( ! IN_CHROOT )); then
        if (( ${#FAILED_SCRIPTS[@]} > 0 || ${#SKIPPED_SCRIPTS[@]} > 0 )); then
            log WARN "Phase 1 did not complete cleanly; not initiating Phase 2."
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

        # Release the lock BEFORE crossing the boundary to prevent FD inheritance hangs
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
            log OK "Autonomous finish flag detected from 011_exiting_unmounting.sh."
            log INFO "Unmounting filesystems securely..."
            umount -R "$CHROOT_MNT"
            log OK "All filesystems flushed and unmounted."
            printf "\n%s>>> POWERING OFF IN 5 SECONDS. PULL YOUR USB DRIVE WHEN SCREEN GOES BLACK. <<<%s\n" "$Y" "$RS"
            sleep 5
            poweroff
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
