#!/usr/bin/env bash
# ==============================================================================
# ASUSCTL BATTERY OPTIMIZATION DEPLOYMENT SCRIPT (V10)
# Target: ASUS TUF F15 (FX507ZE)
# Features: Idempotent state-tracking, interactive prompts, and --force override.
# Fixes: Corrects the --force logic gate to properly override matching states.
# ==============================================================================

set -o errexit
set -o nounset
set -o pipefail
shopt -s nullglob inherit_errexit

# ------------------------------------------------------------------------------
# 1. Configuration & Variables
# ------------------------------------------------------------------------------
declare -r SCRIPT_PATH="${BASH_SOURCE[0]}"
declare -r ASUS_DIR="/etc/asusd"
declare -r BACKUP_DIR="${ASUS_DIR}/backups"

declare -ar CONFIG_FILES=(
    "asusd.ron"
    "aura_tuf.ron"
    "fan_curves.ron"
)

declare FORCE_MODE=false

if [[ -t 1 ]]; then
    declare -r RED=$'\e[1;31m'
    declare -r GREEN=$'\e[1;32m'
    declare -r YELLOW=$'\e[1;33m'
    declare -r BLUE=$'\e[1;34m'
    declare -r RESET=$'\e[0m'
else
    declare -r RED='' GREEN='' YELLOW='' BLUE='' RESET=''
fi

# ------------------------------------------------------------------------------
# 2. Helper Functions
# ------------------------------------------------------------------------------
log_info()    { printf '%s[INFO]%s %s\n'    "${BLUE}"   "${RESET}" "$1"; }
log_success() { printf '%s[SUCCESS]%s %s\n' "${GREEN}"  "${RESET}" "$1"; }
log_warn()    { printf '%s[WARN]%s %s\n'    "${YELLOW}" "${RESET}" "$1"; }
log_error()   { printf '%s[ERROR]%s %s\n'   "${RED}"    "${RESET}" "$1" >&2; }
fail()        { log_error "$1"; exit "${2:-1}"; }

auto_elevate() {
    (( EUID == 0 )) && return 0
    log_warn "This script requires root privileges to modify ${ASUS_DIR}."
    command -v sudo >/dev/null 2>&1 || fail "sudo is required but not installed."
    log_info "Attempting to elevate privileges via sudo..."
    exec sudo -- "${BASH}" "${SCRIPT_PATH}" "$@"
    fail "Unable to elevate with sudo."
}

# ------------------------------------------------------------------------------
# 3. Configuration Payloads
# ------------------------------------------------------------------------------
get_file_content() {
    local -r filename="$1"
    case "${filename}" in
        "asusd.ron")
            cat <<'EOF'
(
    charge_control_end_threshold: 75,
    base_charge_control_end_threshold: 0,
    disable_nvidia_powerd_on_battery: true,
    ac_command: "",
    bat_command: "",
    platform_profile_linked_epp: true,
    platform_profile_on_battery: Quiet,
    change_platform_profile_on_battery: true,
    platform_profile_on_ac: Quiet,
    change_platform_profile_on_ac: true,
    profile_quiet_epp: Power,
    profile_balanced_epp: BalancePower,
    profile_custom_epp: Performance,
    profile_performance_epp: Performance,
    ac_profile_tunings: {
        Quiet: (enabled: true, group: {}),
        Balanced: (enabled: false, group: {}),
        Performance: (enabled: false, group: {}),
    },
    dc_profile_tunings: {
        Quiet: (enabled: true, group: {}),
        Balanced: (enabled: false, group: {}),
        Performance: (enabled: false, group: {}),
    },
    armoury_settings: {
        PanelOverdrive: 0,
    },
)
EOF
            ;;
        "aura_tuf.ron")
            cat <<'EOF'
(
    config_name: "aura_tuf.ron",
    brightness: Off,
    current_mode: Static,
    builtins: {
        Static: (mode: Static, zone: r#None, colour1: (r: 255, g: 255, b: 0), colour2: (r: 0, g: 0, b: 0), speed: Med, direction: Right),
        Breathe: (mode: Breathe, zone: r#None, colour1: (r: 166, g: 0, b: 0), colour2: (r: 0, g: 0, b: 0), speed: Med, direction: Right),
        RainbowCycle: (mode: RainbowCycle, zone: r#None, colour1: (r: 166, g: 0, b: 0), colour2: (r: 0, g: 0, b: 0), speed: Med, direction: Right),
        RainbowWave: (mode: RainbowWave, zone: r#None, colour1: (r: 166, g: 0, b: 0), colour2: (r: 0, g: 0, b: 0), speed: Med, direction: Right),
        Pulse: (mode: Pulse, zone: r#None, colour1: (r: 166, g: 0, b: 0), colour2: (r: 0, g: 0, b: 0), speed: Med, direction: Right),
    },
    multizone_on: false,
    enabled: (states: [(zone: Keyboard, boot: false, awake: false, sleep: false, shutdown: false)]),
)
EOF
            ;;
        "fan_curves.ron")
            cat <<'EOF'
(
    profiles: (
        balanced: [
            (fan: CPU, pwm: (40, 66, 84, 84, 109, 147, 155, 181), temp: (57, 59, 62, 67, 70, 73, 75, 77), enabled: false),
            (fan: GPU, pwm: (33, 40, 63, 86, 94, 117, 170, 186), temp: (57, 59, 63, 66, 69, 72, 74, 77), enabled: false),
        ],
        performance: [
            (fan: CPU, pwm: (53, 84, 84, 109, 147, 155, 181, 198), temp: (30, 55, 59, 62, 65, 67, 70, 72), enabled: false),
            (fan: GPU, pwm: (35, 63, 86, 94, 117, 170, 186, 201), temp: (30, 55, 60, 63, 66, 69, 72, 74), enabled: false),
        ],
        quiet: [
            (fan: CPU, pwm: (40, 66, 84, 84, 109, 147, 147, 147), temp: (58, 61, 64, 67, 70, 73, 76, 76), enabled: false),
            (fan: GPU, pwm: (33, 40, 63, 86, 94, 117, 117, 117), temp: (58, 62, 65, 66, 69, 73, 77, 77), enabled: false),
        ],
        custom: [],
    ),
)
EOF
            ;;
        *) fail "Unknown configuration template requested: ${filename}" ;;
    esac
}

# ------------------------------------------------------------------------------
# 4. Parse Arguments
# ------------------------------------------------------------------------------
parse_args() {
    while [[ $# -gt 0 ]]; do
        case "$1" in
            -f|--force) FORCE_MODE=true; shift ;;
            -h|--help) echo "Usage: $0 [--force]"; exit 0 ;;
            *) fail "Unknown option: $1" ;;
        esac
    done
}

# ------------------------------------------------------------------------------
# 5. Main Execution Engine
# ------------------------------------------------------------------------------
main() {
    parse_args "$@"
    auto_elevate "$@"

    log_info "Starting ASUS battery optimization deployment..."

    [[ ! -d "${ASUS_DIR}" ]] && mkdir -p "${ASUS_DIR}"
    command -v sha256sum >/dev/null 2>&1 || fail "sha256sum is required but not installed."

    printf '%s\n' '--------------------------------------------------------------------------------'
    local restart_required=false

    for file in "${CONFIG_FILES[@]}"; do
        local target_file="${ASUS_DIR}/${file}"
        local state_file="${ASUS_DIR}/.${file}.script_hash"
        local payload_hash
        local deployed_hash=""

        payload_hash=$(get_file_content "${file}" | sha256sum | awk '{print $1}')

        if [[ -f "${state_file}" && -f "${target_file}" ]]; then
            deployed_hash=$(cat "${state_file}")
        fi

        # If hashes match AND we aren't forcing, skip.
        if [[ "${payload_hash}" == "${deployed_hash}" && "${FORCE_MODE}" == false ]]; then
            log_success "Skipping ${file} - Already perfectly up to date."
            continue
        fi

        # If the file exists, we either prompt or backup-and-overwrite
        if [[ -f "${target_file}" ]]; then
            if [[ "${FORCE_MODE}" == true ]]; then
                log_warn "Forced overwrite triggered for ${file} (--force flag set)."
            else
                log_warn "State mismatch for ${file}."
                read -r -p "${YELLOW}[?] Target file differs. Overwrite ${file}? [y/N] ${RESET}" </dev/tty response
                if [[ ! "$response" =~ ^[Yy]$ ]]; then
                    log_info "Skipped: ${file}"
                    continue
                fi
            fi
            
            # Create the backup
            printf -v ts '%(%Y%m%d_%H%M%S)T' -1
            local run_backup_dir="${BACKUP_DIR}/run_${ts}"
            mkdir -p "${run_backup_dir}"
            cp -p "${target_file}" "${run_backup_dir}/${file}.bak"
            log_info "Backed up old ${file} to ${run_backup_dir}/"
        fi

        # Write Payload
        get_file_content "${file}" > "${target_file}"
        chmod 644 "${target_file}"
        chown root:root "${target_file}"
        
        # Update State Tracker
        echo "${payload_hash}" > "${state_file}"
        
        log_success "Deployed: ${file}"
        restart_required=true
    done

    # ------------------------------------------------------------------------------
    # 6. Service & DBus Orchestration
    # ------------------------------------------------------------------------------
    if [[ "${restart_required}" == true ]]; then
        printf '%s\n' '--------------------------------------------------------------------------------'
        
        if command -v asusd >/dev/null 2>&1 && command -v asusctl >/dev/null 2>&1; then
            log_info "Restarting asusd.service to apply configurations..."
            
            if command -v systemctl >/dev/null 2>&1; then
                if systemctl restart asusd.service; then
                    log_success "asusd daemon restarted successfully."
                    
                    sleep 3 # Give daemon extra time to fully map DBus properties
                    
                    log_info "Applying hardware power clamps via DBus CLI..."
                    asusctl profile set Quiet || log_warn "Failed to set Quiet profile."
                    asusctl armoury set ppt_pl1_spl 28 || log_warn "Failed to set ppt_pl1_spl"
                    asusctl armoury set ppt_pl2_sppt 28 || log_warn "Failed to set ppt_pl2_sppt"
                    asusctl armoury set nv_dynamic_boost 5 || log_warn "Failed to set nv_dynamic_boost"
                    log_success "Hardware clamps applied successfully."
                else
                    log_warn "Failed to restart asusd. You may need to manually reboot."
                fi
            else
                log_warn "systemd not detected. Please manually restart asusd."
            fi
        else
            log_warn "The 'asusctl' package is not currently installed."
            log_success "Configurations are safely staged in ${ASUS_DIR}/ and will apply automatically when installed."
        fi
    else
        printf '%s\n' '--------------------------------------------------------------------------------'
        log_info "No configuration changes were made. Services left untouched."
    fi

    log_success "ASUS orchestration complete!"
}

main "$@"
