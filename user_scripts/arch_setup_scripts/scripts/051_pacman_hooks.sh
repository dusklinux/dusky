#!/usr/bin/env bash
# Declares and manages universal ALPM (Pacman) hooks system-wide.

# --- Strict Execution Pragmas ---
set -euo pipefail

# --- Color Support ---
if [[ -t 1 ]]; then
    readonly RED=$'\033[0;31m'
    readonly GREEN=$'\033[0;32m'
    readonly BLUE=$'\033[0;34m'
    readonly YELLOW=$'\033[1;33m'
    readonly GRAY=$'\033[0;90m'
    readonly NC=$'\033[0m'
else
    readonly RED="" GREEN="" BLUE="" YELLOW="" GRAY="" NC=""
fi

# --- Auto-Elevation Logic ---
if (( EUID != 0 )); then
    printf "${BLUE}[INFO]${NC} Root privileges required for ALPM hooks. Elevating...\n" >&2
    # "$@" passes any flags (like --auto) straight through to the elevated shell
    exec sudo /usr/bin/bash "$(realpath "$0")" "$@"
fi

# --- Safe Path Resolution ---
if [[ -n "${SUDO_USER:-}" ]]; then
    readonly REAL_HOME="$(getent passwd "$SUDO_USER" | cut -d: -f6)"
else
    readonly REAL_HOME="$HOME"
fi

# --- System Constants ---
readonly HOOK_DIR="/etc/pacman.d/hooks"

# --- Configuration ---
# Format: "ABSOLUTE_PATH_TO_SOURCE | DEFAULT_ACTION"
# Actions:
#   'install' -> Sync file, verify syntax, set permissions
#   'remove'  -> Delete from system hooks
readonly HOOKS_CONFIG=(
    # Dusky Waybar update counter hook
    "${REAL_HOME}/user_scripts/pacman/hooks/dusky_waybar_updates.hook | install"

    # Add future hooks here:
    # "${REAL_HOME}/user_scripts/pacman/hooks/clean-cache.hook | remove # Inline comments now supported"
)

# --- Helper Functions ---
log_info()    { printf "${BLUE}[INFO]${NC} %s\n" "$1"; }
log_success() { printf "${GREEN}[OK]${NC} %s\n" "$1"; }
log_warn()    { printf "${YELLOW}[WARN]${NC} %s\n" "$1"; }
log_error()   { printf "${RED}[ERROR]${NC} %s\n" "$1" >&2; }
log_skip()    { printf "${GRAY}[SKIP]${NC} %s\n" "$1"; }

# Pure Bash whitespace trimmer
trim() {
    local s="$1"
    s="${s#"${s%%[![:space:]]*}"}"
    s="${s%"${s##*[![:space:]]}"}"
    printf '%s' "$s"
}

# --- Core Logic ---

install_and_manage() {
    local source_path="$1"
    local default_action="$2"
    local auto_mode="$3"
    local hook_name="${source_path##*/}"
    local target_file="${HOOK_DIR}/${hook_name}"
    local user_input
    local user_choice

    echo "------------------------------------------------"
    log_info "Evaluating Hook: $hook_name"

    # Interactive Prompt Logic vs Auto Mode
    if [[ "$auto_mode" == "true" ]]; then
        log_info "Auto-mode active. Enforcing default state: $default_action"
        user_input=""
    else
        local prompt_msg
        if [[ "$default_action" == "install" ]]; then
            prompt_msg="Install/Sync ${hook_name}? [Y/n] (Default: Yes): "
        else
            prompt_msg="Install/Sync ${hook_name}? [y/N] (Default: No): "
        fi
        printf "${YELLOW}%s${NC}" "$prompt_msg"
        read -r user_input || true
    fi

    # Fallback to default if input is empty
    if [[ -z "$user_input" ]]; then
        [[ "$default_action" == "install" ]] && user_choice="y" || user_choice="n"
    else
        user_choice="${user_input,,}"
    fi

    # Execution Router
    case "$user_choice" in
        y|yes)
            # 1. Existence Check
            if [[ ! -f "$source_path" ]]; then
                log_error "Source file missing: $source_path"
                return 0
            fi

            # 2. Extension Check (Pacman requirement)
            if [[ "${hook_name##*.}" != "hook" ]]; then
                log_error "Invalid extension. ALPM hooks MUST end in '.hook'."
                return 0
            fi

            # 3. Syntax Pre-flight Check
            if ! grep -q '\[Trigger\]' "$source_path" || ! grep -q '\[Action\]' "$source_path"; then
                log_error "Malformed payload. Missing [Trigger] or [Action] headers."
                return 0
            fi

            # 4. Idempotency Check (True State Sync)
            if [[ -f "$target_file" ]] && cmp -s "$source_path" "$target_file"; then
                local current_perms
                current_perms=$(stat -c "%a" "$target_file")
                if [[ "$current_perms" == "644" ]]; then
                    log_skip "State identical. Already up-to-date."
                    return 0
                fi
            fi
            
            # 5. Deployment
            log_info "Deploying and enforcing permissions to $target_file..."
            install -D -m 644 -o root -g root -- "$source_path" "$target_file"
            log_success "Hook deployed successfully."
            ;;
        *)
            # Removal Logic
            if [[ -f "$target_file" ]]; then
                rm -f "$target_file"
                log_success "Hook purged from system."
            else
                log_skip "Hook already absent. No action taken."
            fi
            ;;
    esac
}

main() {
    local auto_mode="false"
    
    # Parse CLI flags
    for arg in "$@"; do
        if [[ "$arg" == "--auto" || "$arg" == "--default" ]]; then
            auto_mode="true"
        fi
    done

    if [[ ${#HOOKS_CONFIG[@]} -eq 0 ]]; then
        log_warn "Configuration array HOOKS_CONFIG is empty."
        exit 0
    fi

    # Ensure parent directory exists with strict standard permissions
    if [[ ! -d "$HOOK_DIR" ]]; then
        mkdir -p -m 755 "$HOOK_DIR"
    fi

    log_info "Initializing ALPM Hook State Manager..."
    
    for entry in "${HOOKS_CONFIG[@]}"; do
        entry="${entry%%#*}"
        [[ -z "${entry// /}" ]] && continue
        
        IFS='|' read -r src_path action <<< "$entry"
        
        src_path=$(trim "$src_path")
        action=$(trim "$action")

        if [[ "$action" != "install" && "$action" != "remove" ]]; then
             log_warn "Invalid action '$action' for $src_path. Forcing 'remove' for safety."
             action="remove"
        fi

        # Pass the auto flag status into the worker function
        install_and_manage "$src_path" "$action" "$auto_mode"
    done

    echo "------------------------------------------------"
    log_success "Infrastructure state enforcement complete."
}

# --- Cleanup Trap ---
cleanup() {
    local exit_code=$?
    if [[ $exit_code -ne 0 ]]; then
        log_error "Execution interrupted or failed (Exit: $exit_code)."
    fi
}
trap cleanup EXIT
trap 'exit 130' INT

main "$@"
