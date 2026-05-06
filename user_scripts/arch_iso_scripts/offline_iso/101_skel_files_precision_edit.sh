#!/usr/bin/env bash
# ==============================================================================
# orchestra_state_manager.sh - Multi-Payload State Injector/Exciser
# Target: Arch Linux (Bash 5.3+) | Wayland/Hyprland Ecosystem
# Architecture: Zero-Corruption Atomic Writes, Smart Trim Pipe Delimiter
# ==============================================================================

set -euo pipefail

# --- ANSI TERMINAL CONSTANTS ---
readonly C_BOLD=$'\033[1m'
readonly C_BLUE=$'\033[34m'
readonly C_GREEN=$'\033[32m'
readonly C_RED=$'\033[31m'
readonly C_RESET=$'\033[0m'

log_info() { printf "%s[INFO]%s %s\n" "${C_BLUE}${C_BOLD}" "${C_RESET}" "${*:-}"; }
log_ok()   { printf "%s[OK]%s %s\n" "${C_GREEN}${C_BOLD}" "${C_RESET}" "${*:-}"; }
log_err()  { printf "%s[ERROR]%s %s\n" "${C_RED}${C_BOLD}" "${C_RESET}" "${*:-}" >&2; }

# ==============================================================================
# EDIT HERE: USER CONFIGURATION (MULTI-TARGET PAYLOAD)
# ==============================================================================
# FORMAT: 'relative/path/to/file | Exact string to inject or remove'
#
# SPACING IS ALLOWED: You can put as many spaces around the pipe '|' as you want 
# for readability. The script will automatically trim them away.
#
# STRONG QUOTES: Wrap the entire line in single quotes (' '). This allows you 
# to use double quotes (" ") inside the payload without needing backslashes.

readonly -a PAYLOADS=(
    # 1. Target autostart.conf (Spaces around the pipe are completely safe now)
    '.config/hypr/source/autostart.conf | exec-once = foot --hold --title "Dusky Orchestra" bash -c "~/user_scripts/arch_setup_scripts/ORCHESTRA.sh"'

    # 2. Target the exact same file with another line
#    '.config/hypr/source/autostart.conf | exec-once = echo "Running | logging" > /tmp/log'

    # 3. Target a completely different file
#    '.bash_profile                      | # Dusky auto-injection test line'
)

# ==============================================================================
# CORE EXECUTION ENGINE (DO NOT EDIT BELOW)
# ==============================================================================

apply_atomic_state() {
    local action="$1"
    local actual_target="$2"
    local target_line="$3"
    
    local target_dir="${actual_target%/*}"

    # 1. Provisioning
    if [[ "$action" == "--inject" ]] && [[ ! -d "${target_dir}" ]]; then
        mkdir -p "${target_dir}"
    fi

    if [[ ! -f "${actual_target}" ]]; then
        if [[ "$action" == "--remove" ]]; then
            log_ok "Missing file. Nothing to remove. Skipping."
            return 0
        fi
        touch "${actual_target}"
    fi

    if [[ ! -w "${actual_target}" ]]; then
        log_err "CRITICAL: Write permission denied for ${actual_target}"
        exit 1
    fi

    # 2. Load File Into Memory Safely
    local existing_lines=()
    mapfile -t existing_lines < "${actual_target}"

    # 3. Evaluate State and Build Buffer
    local output_buffer=()
    local state_changed=0

    if [[ "$action" == "--inject" ]]; then
        local found=0
        for l in "${existing_lines[@]}"; do
            output_buffer+=("$l")
            if [[ "$l" == "${target_line}" ]]; then
                found=1
            fi
        done
        if [[ $found -eq 0 ]]; then
            output_buffer+=("${target_line}")
            state_changed=1
        fi

    elif [[ "$action" == "--remove" ]]; then
        for l in "${existing_lines[@]}"; do
            if [[ "$l" != "${target_line}" ]]; then
                output_buffer+=("$l")
            else
                state_changed=1
            fi
        done
    fi

    # 4. Fast exit if no changes needed
    if [[ $state_changed -eq 0 ]]; then
        log_ok "Idempotent. State is already correct."
        return 0
    fi

    # 5. Secure Temporary Block Allocation
    local temp_file
    temp_file=$(mktemp "${target_dir}/.hyprland.conf.XXXXXX") || {
        log_err "Failed to allocate temporary file descriptor."
        exit 1
    }

    command cp -pf "${actual_target}" "${temp_file}"

    # 6. Truncate temp file, then Batch Buffer Flush
    > "${temp_file}"
    if [[ ${#output_buffer[@]} -gt 0 ]]; then
        if ! printf "%s\n" "${output_buffer[@]}" >> "${temp_file}"; then
            log_err "I/O failure during buffer flush."
            rm -f "${temp_file}"
            exit 1
        fi
    fi

    # 7. Targeted VFS Cache Flush
    if ! sync "${temp_file}"; then
        log_err "Kernel rejected physical block sync for ${temp_file}"
        rm -f "${temp_file}"
        exit 1
    fi

    # 8. The Atomic Rename
    if ! command mv -f "${temp_file}" "${actual_target}"; then
        log_err "Atomic swap failed."
        rm -f "${temp_file}"
        exit 1
    fi

    log_ok "Successfully executed ${action} via atomic write."
}

usage() {
    echo -e "\nUsage: ${0##*/} [--inject | --remove]\n"
    exit 1
}

main() {
    local action=""
    
    while [[ $# -gt 0 ]]; do
        case "${1}" in
            --inject|--remove)
                if [[ -n "${action}" ]]; then
                    log_err "Only one action can be specified at a time."
                    usage
                fi
                action="${1}"
                shift
                ;;
            *)
                log_err "Unknown argument: ${1}"
                usage
                ;;
        esac
    done

    if [[ -z "${action}" ]]; then
        log_err "No action specified."
        usage
    fi

    echo -e "${C_BOLD}==> INITIATING MULTI-PAYLOAD STATE MANAGER (${action})${C_RESET}"

    for entry in "${PAYLOADS[@]}"; do
        # 1. Extract raw path and payload
        local raw_path="${entry%%|*}"
        local raw_line="${entry#*|}"

        if [[ -z "${raw_path}" || -z "${raw_line}" || "${raw_path}" == "${entry}" ]]; then
            log_err "Malformed payload entry detected (missing pipe delimiter). Skipping."
            continue
        fi

        # 2. SMART TRIM: Pure Bash parameter expansion to strip leading/trailing spaces
        local rel_path="${raw_path#"${raw_path%%[![:space:]]*}"}"
        rel_path="${rel_path%"${rel_path##*[![:space:]]}"}"

        local target_line="${raw_line#"${raw_line%%[![:space:]]*}"}"
        target_line="${target_line%"${target_line##*[![:space:]]}"}"

        # 3. Dynamic Path Resolution (Root/Skel vs User/Home)
        local target_file=""
        if [[ "$(whoami)" == "root" && -d "/etc/skel" ]]; then
            target_file="/etc/skel/${rel_path}"
        else
            target_file="${HOME}/${rel_path}"
        fi

        # 4. Resolve Symlinks securely
        local actual_target="${target_file}"
        if [[ -L "${target_file}" ]]; then
            actual_target=$(realpath -m "${target_file}")
            log_info "Symlink detected for [${rel_path}]. Target: ${actual_target}"
        fi

        log_info "Evaluating [${rel_path}]..."
        apply_atomic_state "${action}" "${actual_target}" "${target_line}"
    done
    
    echo -e "${C_GREEN}${C_BOLD}==> STATE MANAGEMENT COMPLETE${C_RESET}"
}

main "$@"
