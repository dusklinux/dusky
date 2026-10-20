#!/usr/bin/env bash
#d: Download dusky wallpapers and install them

set -euo pipefail

# --- Configuration -----------------------------------------------------------
readonly ZIP_URL="https://github.com/dusklinux/images/archive/refs/heads/main.zip"
readonly TARGET_PARENT="${XDG_PICTURES_DIR:-${HOME:?HOME not set}/Pictures}"
readonly WALLPAPERS_DIR="${TARGET_PARENT}/wallpapers"
readonly CACHE_DIR="${TARGET_PARENT}/.dusk-wallpapers-cache"
readonly CACHE_FILE="${CACHE_DIR}/dusk-wallpapers.zip"
readonly PART_FILE="${CACHE_FILE}.part"
readonly THEME_STATE="${HOME:?HOME not set}/.config/dusky/settings/dusky_theme/state"

WORK_DIR=""
INSTALLING=0
PENDING_SIGNAL=0
declare -a OLD_PATHS=() NEW_SOURCES=() NEW_TARGETS=()

# --- Terminal Setup (graceful degradation) -----------------------------------
if [[ -t 1 ]]; then
    readonly RST=$'\033[0m' BOLD=$'\033[1m'
    readonly RED=$'\033[31m' GRN=$'\033[32m' YEL=$'\033[33m' BLU=$'\033[34m'
    readonly CLR=$'\033[K'
    readonly IS_TTY=1
else
    readonly RST='' BOLD='' RED='' GRN='' YEL='' BLU='' CLR=''
    readonly IS_TTY=0
fi

# --- Logging -----------------------------------------------------------------
log_info()  { printf '%s[INFO]%s %s\n' "${BLU}" "${RST}" "$*"; }
log_ok()    { printf '%s[ OK ]%s %s\n' "${GRN}" "${RST}" "$*"; }
log_warn()  { printf '%s[WARN]%s %s\n' "${YEL}" "${RST}" "$*" >&2; }
log_error() { printf '%s[ERR ]%s %s\n' "${RED}" "${RST}" "$*" >&2; }

# --- Status Indicator --------------------------------------------------------
CURRENT_STATUS=""

status_begin() {
    CURRENT_STATUS="$1"
    if (( IS_TTY )); then
        printf '\r%s[....]%s %s%s' "${BLU}" "${RST}" "${CURRENT_STATUS}" "${CLR}"
    fi
}

status_end() {
    local -r rc=$1
    if (( IS_TTY )); then
        if (( rc == 0 )); then
            printf '\r%s[ OK ]%s %s%s\n' "${GRN}" "${RST}" "${CURRENT_STATUS}" "${CLR}"
        else
            printf '\r%s[FAIL]%s %s%s\n' "${RED}" "${RST}" "${CURRENT_STATUS}" "${CLR}"
        fi
    else
        if (( rc == 0 )); then
            log_ok "${CURRENT_STATUS}"
        else
            log_error "${CURRENT_STATUS}"
        fi
    fi
    CURRENT_STATUS=""
}

# --- Cleanup Trap ------------------------------------------------------------
cleanup() {
    local exit_code=$? index
    local rollback_failed=0
    # A failed diagnostic must not interrupt recovery (for example, stderr on a full disk).
    set +e
    trap - EXIT
    trap '' INT TERM HUP

    if [[ -n "${CURRENT_STATUS}" ]]; then
        status_end 1
    fi

    if (( INSTALLING )); then
        log_warn "Restoring previous wallpaper directories..."
        for (( index=${#NEW_TARGETS[@]}-1; index>=0; index-- )); do
            # A rename can succeed before mv is interrupted and returns failure.
            # The source disappearing tells us that the planned move took place.
            if [[ ! -e "${NEW_SOURCES[index]}" && ! -L "${NEW_SOURCES[index]}" ]]; then
                if ! mv --no-copy --update=none-fail -T -- \
                    "${NEW_TARGETS[index]}" "${WORK_DIR}/new-${index}"; then
                    rollback_failed=1
                fi
            fi
        done
        for (( index=${#OLD_PATHS[@]}-1; index>=0; index-- )); do
            if [[ -e "${WORK_DIR}/old-${index}" ]]; then
                if ! mv --no-copy --update=none-fail -T -- \
                    "${WORK_DIR}/old-${index}" "${OLD_PATHS[index]}"; then
                    rollback_failed=1
                fi
            fi
        done
    fi

    if (( rollback_failed )); then
        log_error "Rollback incomplete. Previous files preserved in: ${WORK_DIR}"
        exit_code=1
    elif [[ -n "${WORK_DIR}" ]]; then
        rm -rf -- "${WORK_DIR}" || log_warn "Could not remove staging directory: ${WORK_DIR}"
    fi
    # Only the process holding the cache lock may remove the partial file.
    if [[ -n "${CACHE_LOCK_FD:-}" ]]; then
        rm -f -- "${PART_FILE}" || log_warn "Could not remove partial download: ${PART_FILE}"
    fi
    if (( exit_code != 0 )); then
        log_error "Script failed (exit ${exit_code})."
        if [[ -f "${CACHE_FILE}" ]]; then
            log_warn "Archive retained for retry: ${CACHE_FILE}"
        fi
    fi
    exit "${exit_code}"
}
trap cleanup EXIT

handle_signal() {
    if (( INSTALLING )); then
        PENDING_SIGNAL=$1
    else
        exit "$1"
    fi
}
trap 'handle_signal 130' INT
trap 'handle_signal 143' TERM
trap 'handle_signal 129' HUP

# --- Dependency Verification -------------------------------------------------
check_deps() {
    local -a missing=()
    local dep
    for dep in curl unzip mkdir mktemp mv rm flock find; do
        command -v "${dep}" &>/dev/null || missing+=("${dep}")
    done

    if (( ${#missing[@]} > 0 )); then
        log_error "Missing dependencies: ${missing[*]}"
        return 1
    fi
    return 0
}

# --- Download ----------------------------------------------------------------
download_archive() {
    # Extraction verifies CRCs, so do not decompress the collection twice.
    if [[ -f "${CACHE_FILE}" ]]; then
        log_info "Using cached archive; integrity will be checked during extraction."
        return 0
    fi

    log_info "Downloading wallpapers (large collection; size varies)..."

    # Restart failed transfers; never treat a partial response as a cache hit.
    if ! curl --fail --location --retry 3 --connect-timeout 30 \
              --speed-limit 1 --speed-time 120 --remove-on-error \
              --output "${PART_FILE}" "${ZIP_URL}"; then
        log_error "Download failed."
        return 1
    fi
    mv -fT -- "${PART_FILE}" "${CACHE_FILE}"
    log_ok "Download complete."
}

# --- Archive Extraction ------------------------------------------------------
extract_archive() {
    status_begin "Extracting and verifying wallpapers"
    # Ignore user extraction defaults such as text conversion or lowercasing.
    if ! UNZIP='' UNZIPOPT='' unzip -qo "${CACHE_FILE}" -d "${WORK_DIR}" </dev/null; then
        status_end 1
        log_error "Extraction failed. Check free space and the archive; remove '${CACHE_FILE}' to download again."
        return 1
    fi
    local theme image
    for theme in dark light; do
        if [[ ! -d "${WORK_DIR}/images-main/${theme}" ||
              -L "${WORK_DIR}/images-main/${theme}" ]]; then
            status_end 1
            log_error "Archive must contain images-main/dark and images-main/light directories."
            return 1
        fi
        # Match theme_ctl's supported formats; README files or empty subdirectories
        # must not qualify a collection for replacement. Stop at the first match.
        if ! image=$(find -L "${WORK_DIR}/images-main/${theme}" -type f \
            \( -iname '*.jpg' -o -iname '*.jpeg' -o -iname '*.png' \
               -o -iname '*.webp' -o -iname '*.gif' \) -print -quit) || [[ -z "$image" ]]; then
            status_end 1
            log_error "No supported wallpaper found in archive's ${theme} collection."
            return 1
        fi
    done
    status_end 0
}

# --- Install Wallpapers ------------------------------------------------------
install_wallpapers() {
    local active=dark stored=light state path index
    local -a slots=(
        "${TARGET_PARENT}/dark" "${TARGET_PARENT}/light"
        "${WALLPAPERS_DIR}/active_theme"
        "${WALLPAPERS_DIR}/dark" "${WALLPAPERS_DIR}/light"
    )

    # theme_ctl publishes true for dark, false for light; a fresh install defaults to dark.
    if [[ -e "${THEME_STATE}" || -L "${THEME_STATE}" ]]; then
        state=$(<"${THEME_STATE}")
        case "$state" in
            true) ;;
            false) active=light; stored=dark ;;
            *) log_error "Invalid theme state: ${THEME_STATE}"; return 1 ;;
        esac
    fi

    for path in "${slots[@]}"; do
        if [[ -L "$path" || ( -e "$path" && ! -d "$path" ) ]]; then
            log_error "Theme slot must be a physical directory: ${path}"
            return 1
        fi
    done

    log_info "Installing wallpapers (active mode: ${active})..."
    # Keep old directories until both replacements are installed. Renames only:
    # copying across filesystems would complicate failure recovery.
    # Do not run theme_ctl mode changes concurrently with this short transaction.
    INSTALLING=1
    for path in "${slots[@]}"; do
        if [[ -d "$path" ]]; then
            index=${#OLD_PATHS[@]}
            OLD_PATHS+=("$path")
            mv --no-copy --update=none-fail -T -- "$path" "${WORK_DIR}/old-${index}"
        fi
    done
    for path in "${WALLPAPERS_DIR}/active_theme" "${TARGET_PARENT}/${stored}"; do
        NEW_SOURCES+=("${WORK_DIR}/images-main/${active}")
        NEW_TARGETS+=("$path")
        mv --no-copy --update=none-fail -T -- "${WORK_DIR}/images-main/${active}" "$path"
        active=$stored
    done
    if (( PENDING_SIGNAL )); then
        exit "${PENDING_SIGNAL}"
    fi
    INSTALLING=0
    log_ok "Both wallpaper collections installed."
}

# --- Main Entry Point --------------------------------------------------------
main() {
    printf '%s:: Dusk Wallpaper Installer%s\n' "${BOLD}" "${RST}"
    printf '   Install curated wallpaper collection? (large download; replaces theme directories)\n'

    if [[ ! -t 0 ]]; then
        log_error "Interactive terminal required."
        return 1
    fi

    local response
    if ! read -r -p "   [y/N] > " response; then
        log_info "No response; aborted."
        return 0
    fi
    case "${response,,}" in
        y|yes) ;;
        *)     log_info "Aborted by user."; return 0 ;;
    esac

    check_deps
    [[ "${TARGET_PARENT}" == /* ]] || {
        log_error "Pictures directory must be an absolute path: ${TARGET_PARENT}"
        return 1
    }
    mkdir -p -- "${TARGET_PARENT}" "${WALLPAPERS_DIR}" "${CACHE_DIR}"

    # Lock the directory and keep it in place so concurrent installers use the same inode.
    local lock_fd
    exec {lock_fd}<"${CACHE_DIR}"
    if ! flock --nonblocking "$lock_fd"; then
        log_error "Another wallpaper installer is running."
        return 1
    fi
    CACHE_LOCK_FD=$lock_fd

    download_archive
    WORK_DIR=$(mktemp -d "${TARGET_PARENT}/.dusk-wallpapers-install.XXXXXX")
    extract_archive
    install_wallpapers
    rm -f -- "${CACHE_FILE}"

    log_ok "Installation complete."
    log_info "Location: ${TARGET_PARENT/#"${HOME}"/\~}"
    return 0
}

main "$@"
