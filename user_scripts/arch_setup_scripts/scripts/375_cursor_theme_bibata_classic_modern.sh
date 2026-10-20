#!/usr/bin/env bash
#d: Install the Bibata cursor theme

set -Eeuo pipefail
shopt -s inherit_errexit
umask 022

# Configuration
readonly THEME_NAME="Bibata-Modern-Classic"
readonly CURSOR_SIZE=18
readonly REPO_URL="https://github.com/ful1e5/Bibata_Cursor"
# HOME is guaranteed by login/PAM/systemd on Arch (pam_env(8), systemd.exec(5)
# SetLoginEnvironment). Fail fast instead of guessing a home directory.
: "${HOME:?HOME must be set}"
# XDG paths must be absolute; ignore relative values as the specification requires.
if [[ ${XDG_DATA_HOME-} != /* ]]; then
    XDG_DATA_HOME="${HOME}/.local/share"
fi
readonly XDG_DATA_HOME
readonly ICON_DIR="${XDG_DATA_HOME}/icons"
readonly THEME_PATH="${ICON_DIR}/${THEME_NAME}"

# Network settings (split: tiny version probe vs multi-MB download)
readonly CURL_CONNECT_TIMEOUT=10
readonly CURL_VERSION_TIMEOUT=15
readonly CURL_DOWNLOAD_TIMEOUT=120
readonly CURL_RETRIES=3
readonly CURL_RETRY_DELAY=2
# Shared curl options. Each call starts with -q to ignore user curlrc settings.
# --retry-all-errors is only safe with curl-managed
# -o <file> (it truncates before retry); never use it with a pipe to tar.
readonly -a CURL_COMMON=(
    --location
    --proto '=https'
    --proto-redir '=https'
    --tlsv1.2
    --max-redirs 5
    --connect-timeout "${CURL_CONNECT_TIMEOUT}"
    --retry "${CURL_RETRIES}"
    --retry-all-errors
    --retry-delay "${CURL_RETRY_DELAY}"
    --retry-max-time 60
)

# Colors
if [[ -z ${NO_COLOR-} ]] && [[ -n ${TERM-} ]] && [[ -t 1 ]] && command -v tput &>/dev/null && [[ $(tput colors 2>/dev/null || echo -1) -ge 8 ]]; then
    BLUE=$(tput setaf 4 2>/dev/null || true)
    GREEN=$(tput setaf 2 2>/dev/null || true)
    YELLOW=$(tput setaf 3 2>/dev/null || true)
    RED=$(tput setaf 1 2>/dev/null || true)
    RESET=$(tput sgr0 2>/dev/null || true)
else
    BLUE='' GREEN='' YELLOW='' RED='' RESET=''
fi

# Logging
log_info()    { printf -- "%s[INFO]%s %s\n" "${BLUE}" "${RESET}" "$*"; }
log_success() { printf -- "%s[OK]%s %s\n" "${GREEN}" "${RESET}" "$*"; }
log_warn()    { printf -- "%s[WARN]%s %s\n" "${YELLOW}" "${RESET}" "$*" >&2; }
log_error()   { printf -- "%s[ERROR]%s %s\n" "${RED}" "${RESET}" "$*" >&2; }

die() { log_error "$*"; exit 1; }

# Friendly diagnostics for unexpected failures (requires -E/errtrace above).
# This never fires for handled errors (if-conditions, ||/&& lists, hyprctl
# fallback), only for genuine bugs that errexit would otherwise kill silently.
trap 'log_error "Unexpected failure (exit $?) at line ${LINENO}: ${BASH_COMMAND}"' ERR

usage() {
    printf -- 'Usage: %s [-h|--help]\n' "${0##*/}"
    printf -- 'Install the Bibata cursor theme (%s) to %s.\n' "${THEME_NAME}" "${ICON_DIR}"
}

check_dependencies() {
    local cmd
    for cmd in curl tar xz mktemp mkdir mv rm chmod; do
        command -v "$cmd" &>/dev/null || die "Missing dependency: $cmd"
    done
}

get_latest_version() {
    local url
    url=$(curl -q -sS "${CURL_COMMON[@]}" --fail \
        --max-time "${CURL_VERSION_TIMEOUT}" \
        -o /dev/null -w '%{url_effective}' \
        "${REPO_URL}/releases/latest") || return 1

    # Strip any query/fragment/trailing slash the redirect may carry.
    url="${url%%\?*}"
    url="${url%%\#*}"
    url="${url%/}"

    [[ $url == "${REPO_URL}/releases/tag/"* ]] || return 1
    local version="${url#"${REPO_URL}/releases/tag/"}"
    # Accept release tags without assuming a particular versioning scheme.
    if [[ ! $version =~ ^[A-Za-z0-9][A-Za-z0-9._+-]*$ ]]; then
        return 1
    fi
    printf -- '%s' "$version"
}

update_default_index() {
    # Xcursor consumers in the Wayland session can use this fallback.
    local default_dir="${ICON_DIR}/default"
    mkdir -p -- "$default_dir" || die "Cannot create ${default_dir}"
    # Stage beside the destination: default/ may point to another filesystem.
    # Register it for EXIT cleanup before writing or changing its permissions.
    INDEX_TEMP=$(mktemp -- "${default_dir}/.index.theme.XXXXXX") || die "Cannot create temp index.theme"
    printf -- '[Icon Theme]\nName=Default\nComment=Default Cursor Theme\nInherits=%s\n' \
        "${THEME_NAME}" > "$INDEX_TEMP" || die "Cannot write index.theme"
    chmod 0644 -- "$INDEX_TEMP" || die "Cannot chmod index.theme"
    mv -fT --no-copy -- "$INDEX_TEMP" "${default_dir}/index.theme" || die "Cannot install index.theme"
    INDEX_TEMP=""
    log_info "Updated default index.theme fallback."
}

# Temporary paths for atomic replacements (cleaned on exit).
STAGING_DIR=""
INDEX_TEMP=""
cleanup_staging() {
    if [[ -n ${INDEX_TEMP:-} ]]; then
        rm -f -- "$INDEX_TEMP" || log_warn "Could not remove temporary index.theme: ${INDEX_TEMP}"
    fi
    if [[ -n "${STAGING_DIR:-}" && -d "${STAGING_DIR}" ]]; then
        rm -rf -- "${STAGING_DIR}" || log_warn "Could not remove staging directory: ${STAGING_DIR}"
    fi
    STAGING_DIR=""
}
trap cleanup_staging EXIT
trap 'exit 130' INT
trap 'exit 143' TERM
trap 'exit 129' HUP

# Main execution
main() {
    (( $# <= 1 )) || die "Expected at most one argument (see --help)."
    case "${1-}" in
        -h|--help) usage; return 0 ;;
        '') ;;
        *) die "Unknown option: $1 (see --help)" ;;
    esac

    log_info "Starting Bibata Cursor setup..."
    check_dependencies

    # --- Version Detection ---
    log_info "Resolving latest version..."
    local latest_ver
    latest_ver=$(get_latest_version) || die "Could not detect latest version from GitHub."

    log_info "Target: ${THEME_NAME} (${latest_ver}) @ ${CURSOR_SIZE}px"

    # --- Preparation ---
    local dl_url="${REPO_URL}/releases/download/${latest_ver}/${THEME_NAME}.tar.xz"
    mkdir -p -- "${ICON_DIR}" || die "Cannot create ${ICON_DIR}"

    # Staging lives inside ICON_DIR on purpose: same filesystem, so the final
    # mv -T below is a true atomic rename (never a cross-device copy).
    STAGING_DIR=$(mktemp -d -- "${ICON_DIR}/.bibata-staging.XXXXXX") || die "Cannot create staging dir"
    local tmpfile="${STAGING_DIR}/dl.tar.xz"
    # --- Atomic download to file (curl-managed -o, so retries are safe) ---
    log_info "Downloading..."
    if ! curl -q -fS "${CURL_COMMON[@]}" \
            --speed-limit 1024 --speed-time 30 \
            --max-time "${CURL_DOWNLOAD_TIMEOUT}" \
            --remove-on-error \
            -o "$tmpfile" "$dl_url"; then
        die "Download failed (existing theme untouched)."
    fi

    # --- Extract into staging, never into the live dir ---
    log_info "Extracting..."
    if ! tar -xJ --no-same-owner --no-same-permissions --no-acls --no-selinux --no-xattrs -f "$tmpfile" -C "${STAGING_DIR}"; then
        die "Extraction failed (existing theme untouched)."
    fi
    local staged_theme="${STAGING_DIR}/${THEME_NAME}"
    [[ -f "${staged_theme}/index.theme" && -s "${staged_theme}/cursors/left_ptr" ]] \
        || die "Archive has unexpected layout (missing index.theme or usable cursors/left_ptr)."

    # GNU coreutils >= 9.5: exchange atomically, including files and symlinks.
    # The old installation stays available until the exchange succeeds, then
    # lives inside staging for EXIT cleanup. No shared backup slot is needed.
    if [[ -e ${THEME_PATH} || -L ${THEME_PATH} ]]; then
        mv -T --exchange --no-copy -- "$staged_theme" "${THEME_PATH}" \
            || die "Atomic exchange failed (existing theme untouched)."
    else
        mv -T --no-copy --update=none-fail -- "$staged_theme" "${THEME_PATH}" \
            || die "Atomic install failed."
    fi

    [[ -d "${THEME_PATH}" ]] || die "Install verification failed."
    log_success "Installed to ${THEME_PATH}"

    # --- Configuration ---
    # Dusky owns every layer (including the default/index.theme
    # fallback) once installed: hand off to it first, so a rerun of this
    # installer can never clobber Dusky state. Fully optional: skipped when
    # Dusky is absent. A failed reconciliation must not overwrite its settings.
    local dusky_script="${HOME}/user_scripts/cursor/color/dusky_cursor.py"
    if [[ -d "${ICON_DIR}/Dusky/cursors" ]] \
        && [[ -f "${dusky_script}" ]]; then
        command -v python3 &>/dev/null || die "Bibata installed; python3 is required to reconcile Dusky."
        log_info "Dusky theme detected; reconciling via dusky_cursor.py..."
        if python3 "${dusky_script}" --apply --quiet; then
            log_success "Cursor settings reconciled via Dusky."
            return 0
        else
            die "Bibata installed, but Dusky reconciliation failed; standalone overrides skipped."
        fi
    fi

    # Standalone Bibata path (no Dusky installed).
    # 1. Update Xcursor fallback
    update_default_index

    # 2. Apply to Hyprland (Live). Persistence across reboots is owned by the
    # Hyprland env config (XCURSOR_THEME/XCURSOR_SIZE), not this installer.
    if command -v hyprctl &>/dev/null && [[ -n "${HYPRLAND_INSTANCE_SIGNATURE:-}" ]]; then
        log_info "Applying to Hyprland..."
        # Xcursor loading skips unchanged name+size. Nudge first so updates
        # reload the installed files, then always attempt the requested size.
        local size response applied=true
        for size in "$((CURSOR_SIZE + 2))" "${CURSOR_SIZE}"; do
            if ! response=$(hyprctl setcursor "${THEME_NAME}" "$size") || [[ $response != ok ]]; then
                applied=false
                log_warn "hyprctl setcursor failed at ${size}px: ${response:-no response}"
            fi
        done
        if [[ $applied == true ]]; then
            log_success "Cursor active."
        fi
    else
        log_warn "Hyprland not running/detected. Cursor installed but not active."
    fi
}

main "$@"
