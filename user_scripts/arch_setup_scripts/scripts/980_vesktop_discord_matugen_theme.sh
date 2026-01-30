#!/usr/bin/env bash
# -----------------------------------------------------------------------------
# Script: 980_vesktop_discord_matugen_theme.sh
# Description: Setup Vesktop/Discord theme symlinks for Matugen integration
# Environment: Arch Linux / Hyprland / UWSM
# -----------------------------------------------------------------------------

# --- Safety & Error Handling ---
set -euo pipefail
IFS=$'\n\t'

# --- Bash Version Guard (Bash 4.0+ required) ---
if ((BASH_VERSINFO[0] < 4)); then
    printf "Error: Bash 4.0+ required\n" >&2
    exit 1
fi

# --- Visual Styling ---
if command -v tput &>/dev/null && (( $(tput colors) >= 8 )); then
    readonly C_RESET=$'\033[0m'
    readonly C_BOLD=$'\033[1m'
    readonly C_BLUE=$'\033[38;5;45m'
    readonly C_GREEN=$'\033[38;5;46m'
    readonly C_MAGENTA=$'\033[38;5;177m'
    readonly C_WARN=$'\033[38;5;214m'
    readonly C_ERR=$'\033[38;5;196m'
else
    readonly C_RESET='' C_BOLD='' C_BLUE='' C_GREEN=''
    readonly C_MAGENTA='' C_WARN='' C_ERR=''
fi

# --- Configuration ---
readonly MATUGEN_THEME_SRC="${HOME}/.config/matugen/generated/midnight-discord.css"
readonly -a TARGET_PATHS=(
    "${HOME}/.config/vesktop/settings/quickCss.css"
    "${HOME}/.var/app/dev.vencord.Vesktop/config/vesktop/settings/quickCss.css"
    "${HOME}/.config/Vencord/settings/quickCss.css"
    "${HOME}/.config/equibop/settings/quickCss.css"
    "${HOME}/.var/app/org.equicord.equibop/config/equibop/settings/quickCss.css"
    "${HOME}/.config/Equicord/settings/quickCss.css"
)

# --- Logging Utilities ---
log_info()    { printf '%b[INFO]%b %s\n' "${C_BLUE}" "${C_RESET}" "$1"; }
log_success() { printf '%b[SUCCESS]%b %s\n' "${C_GREEN}" "${C_RESET}" "$1"; }
log_warn()    { printf '%b[WARNING]%b %s\n' "${C_WARN}" "${C_RESET}" "$1" >&2; }
die()         { printf '%b[ERROR]%b %s\n' "${C_ERR}" "${C_RESET}" "$1" >&2; exit 1; }

# --- Helper Functions ---

preflight() {
    if ((EUID == 0)); then
        die 'This script must be run as a normal user, not Root/Sudo.'
    fi
}

create_symlink() {
    local target="$1"
    local parent_dir
    parent_dir="$(dirname "$target")"
    
    # Check if parent directory exists
    if [[ ! -d "$parent_dir" ]]; then
        log_info "Skipping $target (parent directory doesn't exist)"
        return 0
    fi
    
    # Remove existing file if it's not a symlink or points to wrong location
    if [[ -e "$target" || -L "$target" ]]; then
        if [[ -L "$target" ]]; then
            local current_link
            current_link="$(readlink -f "$target" 2>/dev/null || echo "")"
            if [[ "$current_link" == "$MATUGEN_THEME_SRC" ]]; then
                log_info "Symlink already correct: $target"
                return 0
            fi
        fi
        log_info "Removing existing file: $target"
        rm -f "$target"
    fi
    
    # Create the symlink
    ln -s "$MATUGEN_THEME_SRC" "$target"
    log_success "Created symlink: $target -> $MATUGEN_THEME_SRC"
}

setup_discord_themes() {
    log_info "Setting up Discord/Vesktop theme symlinks..."
    
    # Check if matugen theme source exists
    if [[ ! -f "$MATUGEN_THEME_SRC" ]]; then
        log_warn "Matugen Discord theme not found at: $MATUGEN_THEME_SRC"
        log_info "This is normal if matugen hasn't been run yet."
        log_info "The symlinks will work once matugen generates the theme."
    fi
    
    local created=0
    local skipped=0
    
    for target in "${TARGET_PATHS[@]}"; do
        if create_symlink "$target"; then
            if [[ -L "$target" ]]; then
                ((created++))
            else
                ((skipped++))
            fi
        fi
    done
    
    log_success "Discord theme setup complete!"
    log_info "Created/verified: $created symlinks"
    log_info "Skipped (no parent dir): $skipped locations"
}

# --- Main Execution ---

main() {
    preflight
    
    log_info "${C_BOLD}Vesktop/Discord Matugen Theme Setup${C_RESET}"
    printf '%s\n' "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
    
    setup_discord_themes
    
    printf '%s\n' "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
    log_success "All operations completed successfully!"
}

main "$@"
