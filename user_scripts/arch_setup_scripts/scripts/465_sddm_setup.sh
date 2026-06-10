#!/usr/bin/env bash
# sddm setup

# ==============================================================================
#  Dusky SDDM Theme Setup Script (Platinum SSO Edition)
#  Repository: github.com/dusklinux/sddm_theme
#  Target: Arch Linux (Linux 7.0+, Systemd 260+, Bash 5.3+)
# ==============================================================================

set -euo pipefail

# --- Auto-Elevation -----------------------------------------------------------
if [[ $EUID -ne 0 ]]; then
    if command -v sudo &>/dev/null; then
        printf "Elevating permissions...\n"
        exec sudo "$0" "$@"
    else
        printf "Error: This script requires root privileges and sudo is missing.\n" >&2
        exit 1
    fi
fi

# --- Constants ----------------------------------------------------------------
readonly GREEN='\033[0;32m'
readonly RED='\033[0;31m'
readonly CYAN='\033[0;36m'
readonly YELLOW='\033[1;33m'
readonly RESET='\033[0m'

readonly THEME_NAME="dusky"
readonly REPO_URL="https://github.com/dusklinux/sddm_theme.git"
readonly INSTALL_DIR="/usr/share/sddm/themes/${THEME_NAME}"
readonly CONF_FILE="/etc/sddm.conf.d/10-${THEME_NAME}-theme.conf"
readonly FACES_DIR="/usr/share/sddm/faces"

# --- Runtime State ------------------------------------------------------------
AUTO_MODE="false"
TEMP_DIR=""
AVATAR_TEMP_FILE=""
SOURCE_DIR=""

# --- Logging Functions --------------------------------------------------------
log_info()    { printf '%b[INFO]%b %s\n' "${CYAN}" "${RESET}" "$1"; }
log_success() { printf '%b[OK]%b %s\n' "${GREEN}" "${RESET}" "$1"; }
log_warn()    { printf '%b[WARN]%b %s\n' "${YELLOW}" "${RESET}" "$1" >&2; }
log_error()   { printf '%b[ERROR]%b %s\n' "${RED}" "${RESET}" "$1" >&2; exit 1; }

# --- Cleanup ------------------------------------------------------------------
cleanup() {
    [[ -n "${TEMP_DIR:-}" && -d "${TEMP_DIR}" ]] && rm -rf "${TEMP_DIR}"
    [[ -n "${AVATAR_TEMP_FILE:-}" && -f "${AVATAR_TEMP_FILE}" ]] && rm -f "${AVATAR_TEMP_FILE}"
    return 0
}
trap cleanup EXIT INT TERM

# --- Utility Functions --------------------------------------------------------
require_command() {
    command -v "$1" &>/dev/null || return 1
}

prompt_yes_no() {
    local prompt="$1"
    local choice
    printf '%s (y/N): ' "${prompt}"
    read -r choice
    [[ "${choice}" =~ ^[Yy]$ ]]
}

# --- Core Logic ---------------------------------------------------------------

install_dependencies() {
    log_info "Checking dependencies..."
    require_command pacman || log_error "Pacman not found. This script is designed for Arch Linux."

    # Added GNOME Keyring dependencies to ensure SSO functionality
    local -a pkgs=(sddm qt6-svg qt6-virtualkeyboard qt6-multimedia-ffmpeg imagemagick git gnome-keyring libsecret seahorse)
    local -a needed=()

    for pkg in "${pkgs[@]}"; do
        pacman -Qi "${pkg}" &>/dev/null || needed+=("${pkg}")
    done

    if [[ ${#needed[@]} -gt 0 ]]; then
        log_info "Installing missing dependencies: ${needed[*]}"
        pacman -S --needed --noconfirm "${needed[@]}" || log_error "Failed to install dependencies."
    else
        log_success "All dependencies are installed."
    fi
}

check_conflicts() {
    log_info "Checking for conflicting Display Managers..."

    # 1. Clean Systemd 260+ Symlink Resolution
    local current_dm
    current_dm=$(basename "$(readlink /etc/systemd/system/display-manager.service 2>/dev/null || echo "")" .service || echo "")

    if [[ -n "${current_dm}" && "${current_dm}" != "sddm" ]]; then
        log_warn "Conflicting DM enabled: ${current_dm}"
        if [[ "${AUTO_MODE}" == "true" ]] || prompt_yes_no "Disable ${current_dm}?"; then
            # Systemctl cleanly drops the symlink. No need for manual rm hacks.
            systemctl disable "${current_dm}.service" 2>/dev/null || true
            log_success "${current_dm} disabled."
        else
            log_warn "Proceeding without disabling ${current_dm}. SDDM enablement will force overwrite."
        fi
    fi

    # 2. Resolve running DM conflicts
    local -a dms=(gdm lightdm lxdm ly slim wdm greetd tdm)
    local conflict="false"

    for dm in "${dms[@]}"; do
        if systemctl is-active --quiet "${dm}.service" 2>/dev/null; then
            log_warn "Conflicting DM running: ${dm}"
            conflict="true"
            if [[ "${AUTO_MODE}" == "true" ]] || prompt_yes_no "Disable and stop ${dm}?"; then
                systemctl disable --now "${dm}.service" 2>/dev/null || true
                log_success "${dm} disabled."
            else
                log_warn "Proceeding with ${dm} active. SDDM may fail to start."
            fi
        fi
    done

    if [[ "${conflict}" == "false" ]]; then
        log_success "No conflicting active DMs found."
    fi
}

setup_sddm_service() {
    if systemctl is-enabled --quiet sddm.service 2>/dev/null; then
        log_success "SDDM service is already enabled."
        return
    fi

    # Force daemon reload to sync internal state before enabling
    systemctl daemon-reload

    if [[ "${AUTO_MODE}" == "true" ]]; then
        systemctl enable --force sddm.service || log_error "Failed to enable SDDM."
    else
        printf '\nSDDM is not enabled. You can log in via TTY without it.\n'
        if prompt_yes_no "Enable SDDM to start at boot?"; then
            systemctl enable --force sddm.service || log_error "Failed to enable SDDM."
            log_success "SDDM service enabled."
        fi
    fi
}

get_source_files() {
    if [[ -f "Main.qml" && -f "metadata.desktop" ]]; then
        log_info "Local files detected. Installing from current directory..."
        SOURCE_DIR="."
    else
        log_info "Local files not found. Cloning from GitHub..."
        require_command git || log_error "Git missing. Cannot clone."
        TEMP_DIR=$(mktemp -d)
        git clone --depth 1 "${REPO_URL}" "${TEMP_DIR}" || log_error "Clone failed."
        SOURCE_DIR="${TEMP_DIR}"
    fi
}

install_theme() {
    log_info "Installing theme to ${INSTALL_DIR}..."
    [[ -d "${INSTALL_DIR}" ]] && rm -rf "${INSTALL_DIR}"
    mkdir -p "${INSTALL_DIR}"

    cp -r "${SOURCE_DIR}/"{backgrounds,components,configs,icons,Main.qml,metadata.desktop} "${INSTALL_DIR}/"
    chmod -R 755 "${INSTALL_DIR}"
    log_success "Theme files copied."
}

configure_sddm() {
    log_info "Configuring SDDM..."
    mkdir -p /etc/sddm.conf.d

    cat > "${CONF_FILE}" <<EOF
[Theme]
Current=${THEME_NAME}

[General]
InputMethod=qtvirtualkeyboard
GreeterEnvironment=QML2_IMPORT_PATH=${INSTALL_DIR}/components/,QT_IM_MODULE=qtvirtualkeyboard
EOF

    log_success "Config saved to ${CONF_FILE}"
}

setup_keyring() {
    log_info "Integrating GNOME Keyring with SDDM PAM stack (Systemd 260+ hardened)..."
    local pam_file="/etc/pam.d/sddm"
    local timestamp
    timestamp=$(date +"%Y%m%d_%H%M%S")
    
    if [[ -f "${pam_file}" ]]; then
        cp "${pam_file}" "${pam_file}.bak.${timestamp}"
        log_info "Backed up existing SDDM PAM config."
    fi

    # Write the Arch Linux standard PAM stack for DMs with Keyring injection
    cat > "${pam_file}" << 'EOF'
#%PAM-1.0

# 1. Standard Checks & Keyring Auth
auth       include      system-login
auth       optional     pam_gnome_keyring.so

# 2. Account Management
account    include      system-login

# 3. Password Changes & Keyring Sync
# use_authtok is required here to prevent duplicate password prompts
password   include      system-login
password   optional     pam_gnome_keyring.so use_authtok

# 4. Session Setup & Keyring Unlock
session    include      system-login
EOF

    # Smart bridging: If pam-fde-boot-pw from the 127 script is present, integrate it.
    # This future-proofs SDDM if the user ever enables autologin on a LUKS drive.
    if [[ -f "/usr/lib/security/pam_fde_boot_pw.so" ]]; then
        log_info "Detected pam-fde-boot-pw! Injecting LUKS Zero-Touch SSO bridge..."
        echo "session    optional     pam_fde_boot_pw.so inject_for=gkr" >> "${pam_file}"
    fi

    cat >> "${pam_file}" << 'EOF'
session    optional     pam_gnome_keyring.so auto_start
EOF

    log_info "Applying Systemd overrides for Kernel Keyring inheritance..."
    mkdir -p /etc/systemd/system/sddm.service.d
    cat > /etc/systemd/system/sddm.service.d/keyringmode.conf << 'EOF'
[Service]
KeyringMode=inherit
EOF
    systemctl daemon-reload

    log_success "GNOME Keyring PAM configuration for SDDM applied successfully."
}

setup_avatar() {
    if [[ "${AUTO_MODE}" == "true" ]]; then
        return
    fi

    printf '\n--- Avatar Setup ---\n'
    prompt_yes_no "Do you want to set a user avatar now?" || return 0

    local real_user="${SUDO_USER:-${USER}}"
    local target_user

    printf 'Enter username [%s]: ' "${real_user}"
    read -r target_user
    target_user="${target_user:-${real_user}}"

    if ! id "${target_user}" &>/dev/null; then
        log_warn "User '${target_user}' does not exist. Skipping."
        return
    fi

    local img_path
    printf 'Enter path to image file: '
    read -e -r img_path

    # Tilde expansion
    if [[ "${img_path}" == "~"* ]]; then
        local user_home
        user_home=$(getent passwd "${target_user}" | cut -d: -f6)
        img_path="${user_home}${img_path:1}"
    fi

    if [[ ! -f "${img_path}" ]]; then
        log_warn "Image file not found at: ${img_path}"
        return
    fi

    log_info "Processing image..."
    mkdir -p "${FACES_DIR}"
    AVATAR_TEMP_FILE=$(mktemp)

    if ! magick "${img_path}" -gravity center -crop 1:1 +repage -resize 256x256 "${AVATAR_TEMP_FILE}"; then
        log_warn "Image processing failed."
        return
    fi

    mv "${AVATAR_TEMP_FILE}" "${FACES_DIR}/${target_user}.face.icon"
    chmod 644 "${FACES_DIR}/${target_user}.face.icon"
    AVATAR_TEMP_FILE=""

    log_success "Avatar updated for user '${target_user}'!"
}

# --- Main ---------------------------------------------------------------------

while [[ $# -gt 0 ]]; do
    case "$1" in
        -a|--auto) AUTO_MODE="true"; log_info "Autonomous mode enabled." ;;
        -h|--help) printf "Usage: %s [-a|--auto]\n" "$0"; exit 0 ;;
        *) log_warn "Unknown arg: $1" ;;
    esac
    shift
done

install_dependencies
check_conflicts
setup_sddm_service
get_source_files
install_theme
configure_sddm
setup_keyring
setup_avatar

printf '\n%bSetup Complete!%b\n' "${GREEN}" "${RESET}"
printf 'To test properly, use:\n'
printf 'sudo QML2_IMPORT_PATH=%s/components/ QT_IM_MODULE=qtvirtualkeyboard sddm-greeter-qt6 --test-mode --theme %s\n' "${INSTALL_DIR}" "${INSTALL_DIR}"
