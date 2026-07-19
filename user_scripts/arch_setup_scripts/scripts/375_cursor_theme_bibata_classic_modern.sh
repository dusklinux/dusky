#!/usr/bin/env bash
# Bibata cursor installer
# ==============================================================================
#  BIBATA CURSOR INSTALLER (FINAL OPTIMIZED)
#  - Auto-detects LATEST version from GitHub
#  - Installs to XDG_DATA_HOME (standard: ~/.local/share/icons)
#  - Clean pipe installation (No temp files)
#  - Generates index.theme for legacy app compatibility
#  - Updates Hyprland session live
# ==============================================================================

# 1. Safety & Strict Mode
set -o errexit   # Exit on error
set -o nounset   # Error on undefined variables
set -o pipefail  # Error if any command in a pipe fails
shopt -s inherit_errexit 2>/dev/null || true # Safety for subshells

# 2. Configuration
readonly THEME_NAME="Bibata-Modern-Classic"
readonly CURSOR_SIZE=18
readonly REPO_URL="https://github.com/ful1e5/Bibata_Cursor"
# Respect XDG standard, fallback to ~/.local/share
readonly USER_HOME="${HOME:-$(getent passwd "$(id -un)" | cut -d: -f6 || echo ~)}"
readonly XDG_DATA_HOME="${XDG_DATA_HOME:-${USER_HOME}/.local/share}"
readonly ICON_DIR="${XDG_DATA_HOME}/icons"
readonly THEME_PATH="${ICON_DIR}/${THEME_NAME}"

# Network settings
readonly CURL_TIMEOUT=30
readonly CURL_RETRIES=3

# 3. Colors (Safe & Compact)
if [[ -z ${NO_COLOR-} ]] && [[ -n ${TERM-} ]] && [[ -t 1 ]] && command -v tput >/dev/null 2>&1 && [[ $(tput colors 2>/dev/null || echo -1) -ge 8 ]]; then
    BLUE=$(tput setaf 4)
    GREEN=$(tput setaf 2)
    YELLOW=$(tput setaf 3)
    RED=$(tput setaf 1)
    RESET=$(tput sgr0)
else
    BLUE="" GREEN="" YELLOW="" RED="" RESET=""
fi

# 4. Logging Helpers
log_info()    { printf "%s[INFO]%s %s\n" "${BLUE}" "${RESET}" "$*"; }
log_success() { printf "%s[OK]%s %s\n" "${GREEN}" "${RESET}" "$*"; }
log_warn()    { printf "%s[WARN]%s %s\n" "${YELLOW}" "${RESET}" "$*" >&2; }
log_error()   { printf "%s[ERROR]%s %s\n" "${RED}" "${RESET}" "$*" >&2; }

die() { log_error "$*"; exit 1; }

# 5. Functions
check_dependencies() {
    local cmd
    for cmd in curl tar; do
        command -v "$cmd" &>/dev/null || die "Missing dependency: $cmd"
    done
}

get_latest_version() {
    # Quoted format string prevents expansion issues.
    # We fetch the effective URL after the redirect to find the tag.
    local url
    url=$(curl -Ls -o /dev/null --max-time "$CURL_TIMEOUT" --retry "$CURL_RETRIES" -w '%{url_effective}' "${REPO_URL}/releases/latest")
    
    local version="${url##*/}"
    
    # Basic validation: Must start with 'v' and contain numbers
    if [[ ! "$version" =~ ^v[0-9] ]]; then
        return 1
    fi
    printf "%s" "$version"
}

update_legacy_index() {
    # This ensures apps that don't respect Hyprland env vars (like some GTK2/X11 apps)
    # still see the correct cursor.
    local default_dir="${ICON_DIR}/default"
    mkdir -p "$default_dir"
    cat > "${default_dir}/index.theme" <<EOF
[Icon Theme]
Name=Default
Comment=Default Cursor Theme
Inherits=${THEME_NAME}
EOF
    log_info "Updated legacy index.theme fallback."
}

# 6. Main Execution
main() {
    log_info "Starting Bibata Cursor setup..."
    check_dependencies

    # --- Version Detection ---
    log_info "Resolving latest version..."
    local latest_ver
    latest_ver=$(get_latest_version) || die "Could not detect latest version from GitHub."
    
    log_info "Target: ${THEME_NAME} (${latest_ver}) @ ${CURSOR_SIZE}px"

    # --- Preparation ---
    local dl_url="${REPO_URL}/releases/download/${latest_ver}/${THEME_NAME}.tar.xz"
    mkdir -p "${ICON_DIR}"

    if [[ -d "${THEME_PATH}" ]]; then
        log_warn "Removing existing installation for clean update..."
        rm -rf "${THEME_PATH}"
    fi

    # --- Streamed Install (Pipe) ---
    log_info "Downloading and Extracting..."
    # curl flags: -L (location), -f (fail on 404), -s (silent), -S (show error on fail)
    if curl -LfSS --max-time "$CURL_TIMEOUT" --retry "$CURL_RETRIES" "$dl_url" | tar -xJ -C "${ICON_DIR}"; then
        log_success "Installed to ${THEME_PATH}"
    else
        die "Download or extraction failed."
    fi

    # --- Configuration ---
    # 1. Update legacy fallback
    update_legacy_index

    # 2. Apply to Hyprland (Live)
    if command -v hyprctl &>/dev/null && [[ -n "${HYPRLAND_INSTANCE_SIGNATURE:-}" ]]; then
        log_info "Applying to Hyprland..."
        if hyprctl setcursor "${THEME_NAME}" "${CURSOR_SIZE}" >/dev/null; then
            log_success "Cursor active."
        else
            log_warn "hyprctl failed to set cursor (check logs)."
        fi
    else
        log_warn "Hyprland not running/detected. Cursor installed but not active."
    fi
}

main "$@"
