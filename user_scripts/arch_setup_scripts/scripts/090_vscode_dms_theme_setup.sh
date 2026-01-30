#!/usr/bin/env bash
# ==============================================================================
# VS CODE DMS THEME EXTENSION SETUP
# ==============================================================================
# Description: Installs visual-studio-code-bin and sets up the DMS Theme
#              extension by symlinking from user_scripts to ~/.vscode/extensions/
#
# Usage:
#   ./016_vscode_dms_theme_setup.sh
# ==============================================================================

set -euo pipefail

# --- FORMATTING & VISUALS ---
readonly C_RESET=$'\e[0m'
readonly C_BOLD=$'\e[1m'
readonly C_GREEN=$'\e[32m'
readonly C_YELLOW=$'\e[33m'
readonly C_RED=$'\e[31m'
readonly C_BLUE=$'\e[34m'

log_info() { printf "${C_BLUE}[INFO]${C_RESET}  %s\n" "$1"; }
log_success() { printf "${C_GREEN}[OK]${C_RESET}    %s\n" "$1"; }
log_warn() { printf "${C_YELLOW}[SKIP]${C_RESET}  %s\n" "$1"; }
log_err() { printf "${C_RED}[FAIL]${C_RESET}  %s\n" "$1"; }
log_crit() { printf "${C_RED}${C_BOLD}[ERROR]${C_RESET} %s\n" "$1"; }

# --- CLEANUP TRAP ---
cleanup() {
  printf "%s" "${C_RESET}"
}
trap cleanup EXIT INT TERM

# --- ROOT GUARD (Prevent Sudo) ---
if [[ $EUID -eq 0 ]]; then
  log_crit "Do NOT run this script as root/sudo."
  printf "        VS Code should be installed as a user. Run simply as: ${C_BOLD}./$(basename "$0")${C_RESET}\n"
  exit 1
fi

# --- PATHS ---
USER_SCRIPTS_DIR="${HOME}/user_scripts"
VSCODE_SETUP_SCRIPT="${USER_SCRIPTS_DIR}/vscode/setup_dms_theme.sh"
EXTENSION_SRC="${USER_SCRIPTS_DIR}/vscode/dms-theme"
EXTENSION_DST="${HOME}/.vscode/extensions/danklinux.dms-theme-0.0.3"

# --- MAIN LOGIC ---
main() {
  printf "\n${C_BOLD}Installing VS Code and DMS Theme Extension...${C_RESET}\n"
  printf "${C_BOLD}-------------------------------------------------------${C_RESET}\n\n"

  # Check if VS Code setup script exists in user_scripts
  if [[ ! -f "$VSCODE_SETUP_SCRIPT" ]]; then
    log_warn "VS Code setup script not found at: $VSCODE_SETUP_SCRIPT"
    log_info "Skipping automatic setup. Manual installation required."
    printf "\n"
    return 0
  fi

  # Check if extension source exists
  if [[ ! -d "$EXTENSION_SRC" ]]; then
    log_err "Extension source not found at: $EXTENSION_SRC"
    printf "\n"
    return 1
  fi

  # Create VS Code extensions directory if needed
  log_info "Creating VS Code extensions directory..."
  mkdir -p "${HOME}/.vscode/extensions"
  log_success "Extensions directory ready"

  # Remove existing symlink or directory
  if [[ -L "$EXTENSION_DST" ]]; then
    log_info "Removing existing symlink..."
    rm -f "$EXTENSION_DST"
  elif [[ -d "$EXTENSION_DST" ]]; then
    log_warn "Extension directory already exists at $EXTENSION_DST"
    log_info "Backing up to ${EXTENSION_DST}.backup"
    mv "$EXTENSION_DST" "${EXTENSION_DST}.backup"
  fi

  # Create symlink
  log_info "Creating extension symlink..."
  ln -nfs "$EXTENSION_SRC" "$EXTENSION_DST"
  log_success "Extension symlink created: $EXTENSION_SRC → $EXTENSION_DST"

  # Verify matugen templates
  local matugen_templates="${HOME}/.config/matugen/templates"
  if [[ -d "$matugen_templates" ]]; then
    if [[ -f "${matugen_templates}/vscode-theme-dark.json" ]]; then
      log_success "Dark theme template found"
    else
      log_warn "Dark theme template not found - will be created on first matugen run"
    fi

    if [[ -f "${matugen_templates}/vscode-theme-light.json" ]]; then
      log_success "Light theme template found"
    else
      log_warn "Light theme template not found - will be created on first matugen run"
    fi
  else
    log_warn "Matugen templates directory not found - will be created during setup"
  fi

  printf "${C_BOLD}-------------------------------------------------------${C_RESET}\n"
  log_success "VS Code DMS Theme extension setup complete!"
  printf "\n"

  # Instructions
  echo "Next steps:"
  echo "  1. In VS Code Settings: Workbench → Color Theme"
  echo "  2. Select 'Dynamic Base16 DankShell (Dark)' or '(Light)'"
  echo "  3. Themes will auto-update when you run matugen"
  printf "\n"
}

main "$@"