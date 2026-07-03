#!/usr/bin/env bash
# Installs multiple systemd system services and manages their state.
# Context: Arch Linux / Bash 5.0+

set -euo pipefail

# --- Color Support ---
if [[ -t 1 ]]; then
    readonly RED=$'\033[0;31m'
    readonly GREEN=$'\033[0;32m'
    readonly BLUE=$'\033[0;34m'
    readonly YELLOW=$'\033[1;33m'
    readonly NC=$'\033[0m' # No Color
else
    readonly RED="" GREEN="" BLUE="" YELLOW="" NC=""
fi

# --- Privilege Escalation ---
if [[ $EUID -ne 0 ]]; then
   printf "${BLUE}[INFO]${NC} Escalating privileges to root...\n"
   exec sudo "$0" "$@"
fi

# --- Resolve Real User's Home Directory ---
REAL_HOME="${HOME}"
if [[ -n "${SUDO_USER:-}" ]]; then
    REAL_HOME=$(getent passwd "$SUDO_USER" | cut -d: -f6)
fi

# --- Configuration ---
readonly SERVICES_CONFIG=(
    # RAPL energy permissions setter service (Default: Enable)
    "$HOME/user_scripts/mako_osd/dusky_glance/services/glance_cpu_pkg_watt.service | enable"

    # Dusky CPU Core and Power Limiter Restorer (Default: Enable)
    "$HOME/user_scripts/performance/cpu/service/dusky_cpu.service | enable"
)

readonly SYSTEMD_SYSTEM_DIR="/etc/systemd/system"

# --- Helper Functions ---
log_info()    { printf "${BLUE}[INFO]${NC} %s\n" "$1"; }
log_success() { printf "${GREEN}[OK]${NC} %s\n" "$1"; }
log_warn()    { printf "${YELLOW}[WARN]${NC} %s\n" "$1"; }
log_error()   { printf "${RED}[ERROR]${NC} %s\n" "$1" >&2; }

trim() {
    local s="$1"
    s="${s#"${s%%[![:space:]]*}"}"
    s="${s%"${s##*[![:space:]]}"}"
    printf '%s' "$s"
}

install_and_manage() {
    local source_path="$1"
    local default_action="$2"
    local service_name
    local target_file
    local prompt_msg
    local user_input
    local user_choice

    # Resolve leading ~/ or $HOME or /root/ to REAL_HOME
    if [[ "$source_path" == "~/"* ]]; then
        source_path="${REAL_HOME}/${source_path#"~/"}"
    elif [[ "$source_path" == '$HOME/'* ]]; then
        source_path="${REAL_HOME}/${source_path#'$HOME/'}"
    elif [[ "$source_path" == "/root/"* ]]; then
        source_path="${REAL_HOME}/${source_path#"/root/"}"
    fi

    service_name="${source_path##*/}"
    target_file="${SYSTEMD_SYSTEM_DIR}/${service_name}"

    echo "------------------------------------------------"
    log_info "Processing: $service_name"

    # 1. Validation
    if [[ ! -f "$source_path" ]]; then
        log_error "Source file not found: $source_path"
        log_warn "Skipping..."
        return 0
    fi

    # 2. Installation (Atomic Copy)
    log_info "Installing to $target_file..."
    install -D -m 644 -- "$source_path" "$target_file"

    # 3. Reload Daemon
    systemctl daemon-reload

    # 4. Interactive State Management
    if [[ "${use_defaults:-false}" == "true" ]]; then
        log_info "Auto-applying default action ($default_action)..."
        user_input=""
    else
        if [[ "$default_action" == "enable" ]]; then
            prompt_msg="Enable and Start $service_name? [Y/n] (Default: Yes): "
        else
            prompt_msg="Enable and Start $service_name? [y/N] (Default: No): "
        fi
        printf "${YELLOW}%s${NC}" "$prompt_msg"
        read -r user_input || true
    fi

    if [[ -z "$user_input" ]]; then
        if [[ "$default_action" == "enable" ]]; then
            user_choice="y"
        else
            user_choice="n"
        fi
    else
        user_choice="${user_input,,}"
    fi

    # 5. Execute Action
    case "$user_choice" in
        y|yes)
            log_info "Enabling and Starting..."
            systemctl enable --now "$service_name"
            log_success "$service_name is active."
            ;;
        *)
            log_info "Disabling/Stopping..."
            systemctl disable --now "$service_name" 2>/dev/null || true
            log_success "$service_name is inactive."
            ;;
    esac
}

main() {
    local use_defaults="false"
    for arg in "$@"; do
        if [[ "$arg" == "--default" ]]; then
            use_defaults="true"
        fi
    done

    if ! command -v systemctl &>/dev/null; then
        log_error "Systemd (systemctl) not found. This script requires systemd."
        exit 1
    fi

    if [[ ${#SERVICES_CONFIG[@]} -eq 0 ]]; then
        log_warn "No services configured in SERVICES_CONFIG."
        exit 0
    fi

    log_info "Starting System Service Manager..."
    
    local entry
    local src_path
    local action

    for entry in "${SERVICES_CONFIG[@]}"; do
        IFS='|' read -r src_path action <<< "$entry"
        src_path=$(trim "$src_path")
        action=$(trim "$action")

        [[ -z "$src_path" ]] && continue

        if [[ "$action" != "enable" && "$action" != "disable" ]]; then
             log_warn "Invalid default action '$action' for $src_path. Defaulting to 'disable'."
             action="disable"
         fi

        install_and_manage "$src_path" "$action"
    done

    echo "------------------------------------------------"
    log_success "All operations completed."
}

cleanup() {
    local exit_code=$?
    if [[ $exit_code -ne 0 ]]; then
        log_error "Script failed or was interrupted (Exit Code: $exit_code)."
    fi
}
trap cleanup EXIT
trap 'exit 130' INT

main "$@"
