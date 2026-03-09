#!/usr/bin/env bash
# -----------------------------------------------------------------------------
# Script: link_service_files.sh
# Description: Robustly manages symbolic links for system services (DBus/Systemd).
#              Auto-creates parent directories and handles service reloads.
# Environment: Arch Linux / Bash 5.0+
# -----------------------------------------------------------------------------

# --- Strict Error Handling ---
set -euo pipefail

# --- Color Definitions ---
if [[ -t 1 ]]; then
    readonly RED=$'\033[0;31m'
    readonly GREEN=$'\033[0;32m'
    readonly BLUE=$'\033[0;34m'
    readonly YELLOW=$'\033[1;33m'
    readonly CYAN=$'\033[0;36m'
    readonly NC=$'\033[0m'
else
    # FIX #1: Define all variables to empty strings to prevent 'set -u' crash
    readonly RED="" GREEN="" BLUE="" YELLOW="" CYAN="" NC=""
fi

# ==============================================================================
# CONFIGURATION
# ==============================================================================
# Format: "SOURCE_PATH | TARGET_PATH"

readonly SYMLINK_MAP=(
    # [DBus] Dusky Control Center Activation
    "$HOME/user_scripts/dusky_system/control_center/service/com.github.dusky.controlcenter.service | $HOME/.local/share/dbus-1/services/com.github.dusky.controlcenter.service"
    "$HOME/user_scripts/sliders/service/org.dusky.sliders.service | $HOME/.local/share/dbus-1/services/org.dusky.sliders.service"
)

# ==============================================================================
# CORE LOGIC
# ==============================================================================

# State tracking for triggers
declare -i NEED_DBUS_RELOAD=0
declare -i NEED_DAEMON_RELOAD=0

log_info()    { printf "${BLUE}[INFO]${NC} %s\n" "$1"; }
log_success() { printf "${GREEN}[OK]${NC}   %s\n" "$1"; }
log_warn()    { printf "${YELLOW}[WARN]${NC} %s\n" "$1"; }
log_error()   { printf "${RED}[ERROR]${NC} %s\n" "$1" >&2; }

# FIX #2: Removed 'eval'. Manually handles tilde expansion safely.
resolve_path() {
    local path="$1"
    if [[ "$path" == "~"* ]]; then
        path="${HOME}${path:1}"
    fi
    printf '%s' "$path"
}

trim() {
    local s="$1"
    s="${s#"${s%%[![:space:]]*}"}"
    s="${s%"${s##*[![:space:]]}"}"
    printf '%s' "$s"
}

process_link() {
    local raw_src="$1"
    local raw_target="$2"

    local src_path target_path target_dir

    src_path=$(resolve_path "$raw_src")
    target_path=$(resolve_path "$raw_target")
    target_dir=$(dirname "$target_path")

    echo "------------------------------------------------"
    log_info "Target: ${CYAN}${target_path##*/}${NC}"

    # 1. Source Validation
    if [[ ! -e "$src_path" ]]; then
        log_error "Source missing: $src_path"

        # Smart Suggestion
        local src_dir
        src_dir=$(dirname "$src_path")
        if [[ -d "$src_dir" ]]; then
            log_warn "Did you name it something else? Found in directory:"
            # FIX #7: Used -F to treat dot as literal, preventing regex wildcards
            ls -1 "$src_dir" | grep -F '.service' | sed 's/^/  - /' || echo "  (No .service files found)"
        fi
        return 1
    fi

    # 2. Destination Preparation
    if [[ ! -d "$target_dir" ]]; then
        log_info "Creating directory: $target_dir"
        # FIX #3: Explicit check because '|| true' in main loop disables 'set -e' here
        if ! mkdir -p "$target_dir"; then
            log_error "Failed to create directory: $target_dir"
            return 1
        fi
    fi

    # 3. Check for existing valid link
    if [[ -L "$target_path" ]]; then
        local current_target real_src
        current_target=$(readlink -f "$target_path")
        real_src=$(readlink -f "$src_path")

        if [[ "$current_target" == "$real_src" ]]; then
            log_success "Link already exists and is correct."
            return 0
        fi
        log_warn "Updating existing link (was pointing to $current_target)"
    elif [[ -e "$target_path" ]]; then
        log_warn "File exists at target (not a link). Backing up..."
        if ! mv "$target_path" "${target_path}.bak"; then
            log_error "Failed to back up: $target_path"
            return 1
        fi
    fi

    # 4. Create Symlink
    # FIX #6: Removed "Atomic" claim. 'ln -sfn' is robust, but not technically atomic.
    if ! ln -sfn "$src_path" "$target_path"; then
        log_error "Failed to create symlink: $target_path"
        return 1
    fi
    # FIX #8: Fixed typo "Linked" -> "Link"
    log_success "Link mapped successfully."

    # 5. Detect Triggers
    if [[ "$target_path" == *"/dbus-1/services/"* ]]; then
        NEED_DBUS_RELOAD=1
    elif [[ "$target_path" == *"/systemd/user/"* ]]; then
        NEED_DAEMON_RELOAD=1
    fi
}

main() {
    log_info "Starting Robust Link Manager..."

    if [[ ${#SYMLINK_MAP[@]} -eq 0 ]]; then
        log_warn "Configuration array SYMLINK_MAP is empty."
        exit 0
    fi

    local entry src_def target_def

    for entry in "${SYMLINK_MAP[@]}"; do
        IFS='|' read -r src_def target_def <<< "$entry"

        src_def=$(trim "$src_def")
        target_def=$(trim "$target_def")

        if [[ -n "$src_def" && -n "$target_def" ]]; then
            # FIX #5: '|| true' allows loop to continue on error, but requires
            # explicit error handling inside process_link (which we added).
            process_link "$src_def" "$target_def" || true
        fi
    done

    echo "------------------------------------------------"

    # Post-processing
    if [[ $NEED_DAEMON_RELOAD -eq 1 ]]; then
        log_info "Systemd units changed. Reloading daemon..."
        if systemctl --user daemon-reload; then
            log_success "Systemd reloaded."
        else
            log_error "Failed to reload systemd."
        fi
    fi

    if [[ $NEED_DBUS_RELOAD -eq 1 ]]; then
        # FIX #4: Use busctl for DBus reload (Arch/dbus-broker compatible)
        # dbus-broker normally auto-reloads, but this forces a config flush safely.
        log_info "DBus services changed. Signaling reload..."
        if busctl --user call org.freedesktop.DBus /org/freedesktop/DBus org.freedesktop.DBus ReloadConfig 2>/dev/null; then
            log_success "DBus reloaded."
        else
            log_warn "DBus reload signal failed (dbus-broker typically auto-discovers; this is likely fine)."
        fi
    fi

    log_success "Done."
}

# --- Cleanup Trap ---
trap '[[ $? -ne 0 ]] && log_error "Script interrupted or failed."' EXIT

main "$@"
