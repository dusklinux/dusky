#!/usr/bin/env bash
# ==============================================================================
#  UNIFIED ARCH ORCHESTRATOR WRAPPER (Textual / Python 3 Bootstrapper)
#  Context: Self-aware Phase 1 (ISO) and Phase 2 (Chroot) execution handoff.
# ==============================================================================

# ==============================================================================
#  1. INSTALLER MODE CONFIGURATION
# ==============================================================================
# Set to 1 for pure Offline Installation (skips internet check and network configure scripts).
# Set to 0 for Online Installation (performs internet checks and prompts to configure).
set -Eeuo pipefail
shopt -s inherit_errexit

declare -gi OFFLINE_MODE=1
for ((i=1; i<=$#; i++)); do
    arg="${!i}"
    profile_arg=""
    case "$arg" in
        --online) OFFLINE_MODE=0 ;;
        --profile=*) profile_arg="${arg#*=}" ;;
        --profile)
            next_i=$((i+1))
            profile_arg="${!next_i:-}"
            ;;
    esac
    case "${profile_arg##*/}" in
        [Oo][Nn][Ll][Ii][Nn][Ee]|002_online|002_online.toml) OFFLINE_MODE=0 ;;
    esac
done

# Unbuffer Python outputs ensuring real-time log piping
export PYTHONUNBUFFERED=1

SCRIPT_PATH="$(readlink -f "${BASH_SOURCE[0]}")"
SCRIPT_DIR="${SCRIPT_PATH%/*}"
SCRIPT_NAME="${SCRIPT_PATH##*/}"
readonly SCRIPT_PATH SCRIPT_DIR SCRIPT_NAME
readonly ORCHESTRATOR_PY="${SCRIPT_DIR}/orchestrator.py"
readonly NETWORK_SCRIPT="${SCRIPT_DIR}/online/003_network_connect.sh"

cd "$SCRIPT_DIR"

# Trap to ensure clean exit
cleanup() {
    exec 9>&- || true
    if [[ -n "${TARGET_TMP:-}" && -d "${TARGET_TMP}" ]]; then
        rm -rf -- "${TARGET_TMP}" || true
    fi
}
trap cleanup EXIT

# ==============================================================================
#  2. ENVIRONMENT PASSTHROUGH (Cross-Chroot Bridge)
# ==============================================================================
readonly ENV_PASSTHROUGH_FILE="${SCRIPT_DIR}/.env_passthrough"

if [[ -f "$ENV_PASSTHROUGH_FILE" ]]; then
    while IFS=$'\t' read -r key value_b64 || [[ -n "${key:-}" ]]; do
        [[ -n "${key:-}" ]] || continue
        case "$key" in
            AUTO_MODE|DRY_RUN|ROOT_PASS|USER_PASS|TARGET_HOSTNAME|TARGET_USER|TARGET_TZ|DUSKY_INSTALL_STARTED_MONOTONIC|OFFLINE_MODE)
                if [[ -n "${value_b64:-}" ]]; then
                    decoded_value="$(printf '%s' "$value_b64" | base64 --decode)" || {
                        printf '[ERR]   Invalid passthrough data for %s\n' "$key" >&2
                        exit 1
                    }
                else
                    decoded_value=""
                fi
                printf -v "$key" '%s' "$decoded_value"
                export "${key?}"
                ;;
        esac
    done < "$ENV_PASSTHROUGH_FILE"
fi

# ==============================================================================
#  3. CHROOT AWARENESS & PHASE SETUP
# ==============================================================================
declare -gi IN_CHROOT=0
declare -g PHASE_FLAG=""

ROOT_STAT="$(stat -c '%d:%i' / 2>/dev/null || true)"
INIT_ROOT_STAT="$(stat -c '%d:%i' /proc/1/root/. 2>/dev/null || true)"
readonly ROOT_STAT INIT_ROOT_STAT

if [[ -n "$ROOT_STAT" && -n "$INIT_ROOT_STAT" && "$ROOT_STAT" != "$INIT_ROOT_STAT" ]]; then
    IN_CHROOT=1
    PHASE_FLAG="--phase2"
else
    IN_CHROOT=0
    PHASE_FLAG="--phase1"
fi

# ==============================================================================
#  4. VISUALS & LOGGING
# ==============================================================================
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

# arch-chroot bind-mounts /run into /mnt/run, allowing target-only tmpfiles
# (e.g. 50-plymouth.conf) to leak hooks into the live ISO's shutdown config;
# replaying live systemd-tmpfiles runs 20-mkinitcpio.conf's f+ rule to restore baseline.
restore_live_shutdown_ramfs_conf() {
    (( IN_CHROOT == 0 )) || return 0
    command -v systemd-tmpfiles >/dev/null 2>&1 || return 0
    [[ -f /run/initramfs/mkinitcpio-shutdown.conf ]] || return 0

    if ! systemd-tmpfiles --create --prefix=/run/initramfs/mkinitcpio-shutdown.conf; then
        log "WARN" "Failed to restore /run/initramfs/mkinitcpio-shutdown.conf on live ISO."
    fi
    return 0
}

# Inspection and marker maintenance do not need package installation,
# networking, or a chroot boundary crossing.
declare -a phase_args=("$PHASE_FLAG")
for arg in "$@"; do
    if [[ "$arg" == --phase1 || "$arg" == --phase2 ]]; then
        PHASE_FLAG="$arg"
        phase_args=()
        break
    fi
done
for arg in "$@"; do
    case "$arg" in
        --help|-h|--list-profiles|--list-scripts|--list-once|--forget-once|--forget-once=*|--doctor|--explain|--dry-run|-d)
            if ! command -v python3 >/dev/null 2>&1; then
                log ERR "Python 3 is required for this command."
                exit 1
            fi
            exec env PYTHONDONTWRITEBYTECODE=1 python3 "$ORCHESTRATOR_PY" "${phase_args[@]}" "$@"
            ;;
    esac
done

if (( EUID != 0 )); then
    log ERR "Run this installer as root."
    exit 1
fi
if (( IN_CHROOT )) && [[ "$PHASE_FLAG" == --phase1 ]]; then
    log ERR "Phase 1 must run in the live ISO environment."
    exit 1
fi

# Keep bootstrap, execution and the boundary crossing under one wrapper lock.
exec 9>"/tmp/dusky_installer_${PHASE_FLAG#--}.lock"
if ! flock --nonblock 9; then
    log ERR "Another installer wrapper is already running for this phase."
    exit 1
fi

if command -v python3 >/dev/null 2>&1; then
    if (( IN_CHROOT == 0 )) || [[ -z "${DUSKY_INSTALL_STARTED_MONOTONIC:-}" ]]; then
        DUSKY_INSTALL_STARTED_MONOTONIC="$(python3 -c 'import time; print(time.monotonic())')"
    fi
    export DUSKY_INSTALL_STARTED_MONOTONIC
fi
export DUSKY_INSTALL_WRAPPER=1
if command -v python3 >/dev/null 2>&1; then
    DUSKY_VALIDATE_ARGS_ONLY=1 python3 "$ORCHESTRATOR_PY" "${phase_args[@]}" "$@" 9>&-
fi

# ==============================================================================
#  5. INTERNET CONNECTIVITY CHECK
# ==============================================================================
check_internet() {
    if command -v curl >/dev/null 2>&1; then
        curl -fsS --connect-timeout 2 --max-time 5 https://archlinux.org >/dev/null 2>&1 && return 0
    fi
    if command -v wget >/dev/null 2>&1; then
        wget -q --tries=1 --timeout=5 -O /dev/null https://archlinux.org >/dev/null 2>&1 && return 0
    fi
    if ! command -v curl >/dev/null 2>&1 && ! command -v wget >/dev/null 2>&1; then
        log ERR "curl or wget is required to verify online connectivity."
    fi
    return 1
}

# Network connection flow (only executed in Online Mode and in Phase 1)
if (( IN_CHROOT == 0 )); then
    if (( OFFLINE_MODE == 0 )); then
        log "INFO" "Online Mode configured. Verifying internet connectivity..."
        if ! check_internet; then
            log "WARN" "No internet connection detected."
            if [[ -x "$NETWORK_SCRIPT" ]]; then
                log "INFO" "Launching network configuration script..."
                "$NETWORK_SCRIPT"
                
                if ! check_internet; then
                    log "ERR" "Still no internet connection after network configuration."
                    log "ERR" "Please connect manually and rerun the installer."
                    exit 1
                fi
                log "OK" "Internet connection verified."
            else
                log "ERR" "Network script not found or not executable at: $NETWORK_SCRIPT"
                log "ERR" "Please connect to the internet manually and rerun."
                exit 1
            fi
        else
            log "OK" "Internet connection verified."
        fi
    else
        log "INFO" "Offline Mode configured. Skipping internet connectivity checks."
    fi
fi

# ==============================================================================
#  6. PYTHON CORE & DEPENDENCIES BOOTSTRAPPING
# ==============================================================================
log "INFO" "Verifying Python core and orchestrator UI dependencies..."

python_ok() {
    python3 -c 'import sys; sys.exit(sys.version_info < (3, 14, 7))' >/dev/null 2>&1
}

ui_ok() {
    python3 -c 'import textual, rich; from importlib.metadata import version; import re, sys; parts = tuple(map(int, re.findall(r"\d+", version("textual"))[:3])); sys.exit((parts + (0, 0, 0))[:3] < (8, 2, 8))' >/dev/null 2>&1
}

install_pkgs() {
    if (( OFFLINE_MODE )); then
        log ERR "Offline runtime dependencies must already be installed: $*"
        log ERR "Include them in the ISO and the pacstrap base package list."
        return 1
    fi
    if [[ -e /var/lib/pacman/db.lck ]]; then
        log ERR "Pacman lock exists at /var/lib/pacman/db.lck. Resolve it before retrying."
        return 1
    fi
    pacman -Syu --noconfirm "$@"
}

if ! command -v python3 >/dev/null 2>&1 || ! python_ok; then
    log WARN "Python 3.14.7+ is required."
    install_pkgs python || exit 1
fi
if ! python_ok; then
    log ERR "Python 3.14.7+ is unavailable after package installation."
    exit 1
fi
if ! ui_ok; then
    log WARN "Python UI dependencies are missing or unusable."
    install_pkgs python-textual python-rich || exit 1
fi
if ! ui_ok; then
    log ERR "Python UI dependencies remain unusable."
    exit 1
fi
# If bootstrap installed Python, establish the clock before launching the UI.
if [[ -z "${DUSKY_INSTALL_STARTED_MONOTONIC:-}" ]]; then
    DUSKY_INSTALL_STARTED_MONOTONIC="$(python3 -c 'import time; print(time.monotonic())')"
    export DUSKY_INSTALL_STARTED_MONOTONIC
fi

log "OK" "Python and UI dependencies verified."

# ==============================================================================
#  7. HANDOFF TO PYTHON ORCHESTRATOR
# ==============================================================================
if [[ ! -f "$ORCHESTRATOR_PY" ]]; then
    log "ERR" "Cannot find Python orchestrator at: $ORCHESTRATOR_PY"
    exit 1
fi

export PYTHONUNBUFFERED=1
export PYTHONUTF8=1
export PYTHONDONTWRITEBYTECODE=1

log "INFO" "Handing execution control over to Python Textual UI..."
set +e
python3 "$ORCHESTRATOR_PY" "${phase_args[@]}" "$@" 9>&-
orchestrator_exit=$?
set -e

if (( orchestrator_exit != 0 )); then
    if (( orchestrator_exit == 130 )); then
        log "WARN" "Installation cancelled by user (Ctrl+C)."
    else
        log "ERR" "Orchestrator exited with code: $orchestrator_exit"
    fi
    if (( IN_CHROOT == 0 )); then
        restore_live_shutdown_ramfs_conf
    fi
    exit "$orchestrator_exit"
fi

# ==============================================================================
#  8. CROSS-CHROOT PHASE BOUNDARY TRANSITION (ISO Phase Only)
# ==============================================================================
if (( IN_CHROOT == 0 )) && [[ "$PHASE_FLAG" == --phase1 ]]; then
    readonly CHROOT_MNT="/mnt"
    if ! mountpoint -q "$CHROOT_MNT"; then
        log "ERR" "Target filesystem '$CHROOT_MNT' is not mounted. Cannot proceed to Phase 2."
        exit 1
    fi

    log "OK" "Phase 1 (ISO) completed successfully."
    log "INFO" "Initiating boundary crossing to Phase 2 (Chroot)..."

    TARGET_TMP="$(mktemp -d "${CHROOT_MNT}/root/arch_install_tmp.XXXXXXXX")"
    readonly TARGET_TMP
    readonly TMP_DIR="/root/${TARGET_TMP##*/}"

    log "INFO" "Cloning orchestrator payload to Phase 2 environment..."
    # Copy hidden credentials and all installer files without shell globbing.
    cp -a -- "${SCRIPT_DIR}/." "$TARGET_TMP/"

    if [[ ! -s "${SCRIPT_DIR}/.selected_profile" ]]; then
        log ERR "Phase 1 did not record the selected installer profile."
        exit 1
    fi
    selected_profile="$(cat "${SCRIPT_DIR}/.selected_profile")"
    if python3 -c 'import sys, tomllib; p = tomllib.load(open(sys.argv[1], "rb")); sys.exit("online" not in p.get("profile", {}).get("name", "").lower())' "$selected_profile"; then
        OFFLINE_MODE=0
    fi

    log "INFO" "Securing environment state for boundary crossing..."
    install -m 600 /dev/null "${TARGET_TMP}/.env_passthrough"
    {
        printf 'OFFLINE_MODE\t%s\n' "$(printf '%s' "$OFFLINE_MODE" | base64 --wrap=0)"
        printf 'AUTO_MODE\t%s\n' "$(printf '%s' "${AUTO_MODE:-1}" | base64 --wrap=0)"
        printf 'DRY_RUN\t%s\n' "$(printf '%s' "${DRY_RUN:-0}" | base64 --wrap=0)"
        printf 'ROOT_PASS\t%s\n' "$(printf '%s' "${ROOT_PASS:-}" | base64 --wrap=0)"
        printf 'USER_PASS\t%s\n' "$(printf '%s' "${USER_PASS:-}" | base64 --wrap=0)"
        printf 'TARGET_HOSTNAME\t%s\n' "$(printf '%s' "${TARGET_HOSTNAME:-}" | base64 --wrap=0)"
        printf 'TARGET_USER\t%s\n' "$(printf '%s' "${TARGET_USER:-}" | base64 --wrap=0)"
        printf 'TARGET_TZ\t%s\n' "$(printf '%s' "${TARGET_TZ:-}" | base64 --wrap=0)"
        printf 'DUSKY_INSTALL_STARTED_MONOTONIC\t%s\n' "$(printf '%s' "$DUSKY_INSTALL_STARTED_MONOTONIC" | base64 --wrap=0)"
    } > "${TARGET_TMP}/.env_passthrough"

    log "INFO" "Handing control to arch-chroot..."

    declare -a phase2_args=()
    skip_next=0
    for arg in "$@"; do
        if (( skip_next )); then
            skip_next=0
            continue
        fi
        case "$arg" in
            --phase1|--phase2|--online|--profile=*) ;;
            --profile) skip_next=1 ;;
            *) phase2_args+=("$arg") ;;
        esac
    done
    mkdir -p "${TARGET_TMP}/profiles"
    cp -a -- "$selected_profile" "${TARGET_TMP}/profiles/.handoff.toml"
    phase2_args+=(--phase2 --profile "${TMP_DIR}/profiles/.handoff.toml")

    set +e
    arch-chroot "$CHROOT_MNT" /bin/bash "${TMP_DIR}/${SCRIPT_NAME}" "${phase2_args[@]}" 9>&-
    chroot_exit=$?
    set -e

    restore_live_shutdown_ramfs_conf

    # Check for auto-poweroff request across chroot boundary BEFORE scrubbing payload
    declare -i auto_poweroff_requested=0
    for marker_check in \
        "/tmp/dusky_auto_poweroff" \
        "/etc/dusky_auto_poweroff" \
        "${CHROOT_MNT}/etc/dusky_auto_poweroff" \
        "${CHROOT_MNT}/root/dusky_auto_poweroff" \
        "${CHROOT_MNT}/tmp/dusky_auto_poweroff" \
        "${TARGET_TMP}/dusky_auto_poweroff"; do
        if [[ -f "$marker_check" ]]; then
            auto_poweroff_requested=1
            rm -f "$marker_check" 2>/dev/null || true
        fi
    done

    log "INFO" "Phase 2 execution terminated (Exit Code: $chroot_exit)."
    log "INFO" "Scrubbing temporary payload and sensitive environment data..."
    rm -rf -- "$TARGET_TMP"

    if (( chroot_exit != 0 )); then
        log "ERR" "Phase 2 encountered a fatal error."
        exit "$chroot_exit"
    fi

    printf "\n%s%s=== COMPLETE SYSTEM DEPLOYMENT SUCCESSFUL ===%s\n" "$G" "$HL" "$RS"

    # --- FINAL USER UNMOUNT FLOW ---
    _poweroff_choice="y"
    if (( auto_poweroff_requested )); then
        log "INFO" "Power off requested from orchestrator. Proceeding with graceful unmount and power off..."
        _poweroff_choice="y"
    elif [[ -t 0 ]]; then
        printf "\n"
        read -r -p ">>> Installation complete! Unmount filesystems and power off now? [Y/n]: " _poweroff_choice || _poweroff_choice="y"
    fi

    if [[ "${_poweroff_choice,,}" != "n" && "${_poweroff_choice,,}" != "no" ]]; then
        log "INFO" "Flushing filesystem buffers to disk (sync)..."
        sync

        log "INFO" "Deactivating swap to release kernel filesystem locks..."
        swapoff -a 2>/dev/null || true
        
        log "INFO" "Attempting graceful unmount of filesystems..."
        if umount -R "$CHROOT_MNT" 2>/dev/null; then
            log "OK" "All filesystems flushed and unmounted cleanly."
            printf "\n%s>>> POWERING OFF. PULL YOUR USB DRIVE WHEN SCREEN GOES BLACK. <<<%s\n" "$Y" "$RS"
            sleep 2
            poweroff
            exit 0
        else
            log "WARN" "Target is busy. Graceful unmount failed."
            log "INFO" "Identifying background processes currently holding the mount hostage:"
            
            printf "\n%s" "$Y"
            if command -v fuser >/dev/null 2>&1; then
                fuser -vmM "$CHROOT_MNT" || true
            else
                printf "  [Cannot list processes: 'fuser' not found on host]\n"
            fi
            printf "%s\n" "$RS"
            
            _force_choice="n"
            if [[ -t 0 ]]; then
                printf "%s[!] WARNING:%s Forcefully terminating processes actively writing data CAN cause filesystem corruption.\n" "$R" "$RS"
                printf "It is often safer to drop to manual mode or let the OS shutdown sequence handle them.\n"
                read -r -p ">>> Do you want to FORCEFULLY terminate these processes and retry unmounting? [y/N]: " _force_choice || _force_choice="n"
            fi
            
            if [[ "${_force_choice,,}" == "y" || "${_force_choice,,}" == "yes" ]]; then
                log "INFO" "Sending graceful termination signals (SIGTERM)..."
                fuser -k -TERM -m -M "$CHROOT_MNT" || true
                sleep 2
                
                log "INFO" "Sending absolute kill signals (SIGKILL)..."
                fuser -k -KILL -m -M "$CHROOT_MNT" || true
                sleep 1
                
                if umount -R "$CHROOT_MNT" 2>/dev/null; then
                    log "OK" "Filesystems forcefully unmounted."
                    printf "\n%s>>> POWERING OFF. PULL YOUR USB DRIVE WHEN SCREEN GOES BLACK. <<<%s\n" "$Y" "$RS"
                    sleep 2
                    poweroff
                    exit 0
                else
                    log "ERR" "Still unable to unmount! A system process is critically locked."
                    _poweroff_choice="n" 
                fi
            else
                log "INFO" "Force unmount aborted by user."
                log "INFO" "Falling back to safe shutdown or manual mode."
                _poweroff_choice="n"
            fi
        fi
    fi

    # Fallback/Manual mode instructions
    if [[ "${_poweroff_choice,,}" == "n" || "${_poweroff_choice,,}" == "no" ]]; then
        log "INFO" "Filesystems remain safely mounted at $CHROOT_MNT."
        printf "\n%s=== MANUAL MODE / POST-INSTALL TWEAKS ===%s\n" "$B" "$RS"
        printf "To re-enter your new system to make manual adjustments, run:\n"
        printf "  %sarch-chroot %s%s\n\n" "$Y" "$CHROOT_MNT" "$RS"
        
        printf "%s[!] CRITICAL: When you are finished, you MUST run these exact commands%s\n" "$R" "$RS"
        printf "%sto flush data to the disk before pulling the USB drive:%s\n" "$R" "$RS"
        printf "  1. %ssync%s\n" "$Y" "$RS"
        printf "  2. %sswapoff -a%s\n" "$Y" "$RS"
        printf "  3. %sumount -R %s%s\n" "$Y" "$CHROOT_MNT" "$RS"
        printf "  4. %spoweroff%s\n\n" "$Y" "$RS"
        
        log "INFO" "Returning control to Live ISO shell. Have fun!"
    fi
fi
