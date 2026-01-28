#!/usr/bin/env bash
# Downloads and installs Dusk wallpapers for Arch/Hyprland.
# Context: Arch Linux, Hyprland, UWSM.
# -----------------------------------------------------------------------------

# --- 1. Safety & Environment ---
set -euo pipefail
IFS=$'\n\t'

# --- 2. Visuals & Logging ---
# Define colors only if running in a terminal (TTY)
if [[ -t 1 ]]; then
    readonly C_RESET=$'\033[0m'
    readonly C_BOLD=$'\033[1m'
    readonly C_GREEN=$'\033[32m'
    readonly C_BLUE=$'\033[34m'
    readonly C_RED=$'\033[31m'
    readonly C_YELLOW=$'\033[33m'
    readonly IS_TTY=1
else
    readonly C_RESET='' C_BOLD='' C_GREEN='' C_BLUE='' C_RED='' C_YELLOW=''
    readonly IS_TTY=0
fi

log_info()    { printf "${C_BLUE}[INFO]${C_RESET} %s\n" "$*"; }
log_success() { printf "${C_GREEN}[OK]${C_RESET}   %s\n" "$*"; }
log_warn()    { printf "${C_YELLOW}[WARN]${C_RESET} %s\n" "$*" >&2; }
log_error()   { printf "${C_RED}[ERR]${C_RESET}  %s\n" "$*" >&2; }

# --- 3. Configuration ---
readonly REPO_URL="https://github.com/dusklinux/images.git"
readonly TARGET_PARENT="${HOME:?HOME not set}/Pictures"
readonly SUBDIRS=("dark" "light")

# CLONE_DIR will be set dynamically using mktemp
CLONE_DIR=""

# --- 4. Cleanup Trap ---
cleanup() {
    local exit_code=$?
    
    # Securely remove the temporary directory
    if [[ -n "$CLONE_DIR" && -d "$CLONE_DIR" ]]; then
        rm -rf "$CLONE_DIR"
    fi
    
    # Restore cursor visibility if in TTY
    if (( IS_TTY )); then
        tput cnorm 2>/dev/null || true
    fi

    if [[ $exit_code -ne 0 && $exit_code -ne 130 ]]; then
        log_error "Script failed with exit code $exit_code."
    fi
}
trap cleanup EXIT

# --- 5. Helper Functions ---

show_spinner() {
    local pid=$1
    local delay=0.1
    local spinstr='|/-\'
    
    # Do not spin if not in TTY (logs/cron)
    (( IS_TTY )) || return 0
    
    tput civis 2>/dev/null || true # Hide cursor
    printf "${C_BLUE}Downloading resources... ${C_RESET}"
    
    while kill -0 "$pid" 2>/dev/null; do
        printf "[%c]" "${spinstr:0:1}"
        spinstr=${spinstr:1}${spinstr:0:1}
        sleep "$delay"
        printf "\b\b\b"
    done
    
    printf "   \b\b\b\n"
}

# --- 6. Main Logic ---

main() {
    # Header
    printf "${C_BOLD}:: Wallpaper Manager for Arch/Hyprland${C_RESET}\n"
    
    # 6.1 Prompt User
    printf "   Download the handpicked wallpaper collection? Strongly Recommended! (~1.7GB)\n"
    local response
    read -r -p "   [y/N] > " response
    
    if [[ ! "${response,,}" =~ ^y(es)?$ ]]; then
        log_info "Skipping download. You can manually manage wallpapers in $TARGET_PARENT."
        exit 0
    fi

    # 6.2 Pre-flight Checks
    if ! command -v git &> /dev/null; then
        log_error "Git is not installed. Please run: sudo pacman -S git"
        exit 1
    fi

    # Ensure parent directory exists so we can create tmp dir inside it (or /tmp)
    if [[ ! -d "$TARGET_PARENT" ]]; then
        mkdir -p "$TARGET_PARENT"
    fi

    # Secure temp directory creation
    CLONE_DIR=$(mktemp -d "$TARGET_PARENT/.images-tmp.XXXXXX")

    # 6.3 Download (Git Clone)
    log_info "Cloning repository from $REPO_URL..."
    
    # Optimizations:
    # --depth 1: History truncation (shallow clone)
    # --single-branch: Do not fetch other branches (Speed Boost)
    # --progress: Explicitly show progress bar (speed, %, data) to the user
    if ! git clone \
        --depth 1 \
        --single-branch \
        --progress \
        "$REPO_URL" "$CLONE_DIR"; then
            log_error "Download failed."
            log_error "Check your internet connection or try manually:"
            log_error "git clone --depth 1 $REPO_URL"
            exit 1
    fi
    log_success "Download complete."

    # 6.4 Move and Merge
    log_info "Processing files..."

    local dir src dest parent_dir
    for dir in "${SUBDIRS[@]}"; do
        src="$CLONE_DIR/$dir"

        # --- LOGIC START ---
        # 'dark' folder -> ~/Pictures/wallpapers/active_theme
        # 'light' folder -> ~/Pictures/light
        if [[ "$dir" == "dark" ]]; then
            parent_dir="$TARGET_PARENT/wallpapers"
            dest="$parent_dir/active_theme"
        else
            parent_dir="$TARGET_PARENT"
            dest="$parent_dir/$dir"
        fi
        # --- LOGIC END ---

        if [[ -d "$src" ]]; then
            # Ensure the parent directory (e.g., wallpapers) exists
            if [[ ! -d "$parent_dir" ]]; then
                mkdir -p "$parent_dir"
            fi

            if [[ -d "$dest" ]]; then
                log_warn "Directory '$(basename "$dest")' already exists in $(basename "$parent_dir"). Merging..."
                
                if command -v rsync &> /dev/null; then
                    # Rsync is safer and handles merges better
                    rsync -a --ignore-existing "$src/" "$dest/"
                else
                    # Fallback: cp recursive, no-clobber
                    cp -rn "$src/." "$dest/" 2>/dev/null || true
                fi
            else
                # Move the folder and rename it to the target destination
                mv "$src" "$dest"
            fi
            
            log_success "Installed: $dir -> $(basename "$parent_dir")/$(basename "$dest")"
        else
            log_warn "Source directory '$dir' not found in repository."
        fi
    done

    # 6.5 Success
    # Use # anchor to only replace HOME if it's at the start of the string
    log_success "Operation finished."
    log_info "Wallpapers located in: ${TARGET_PARENT/#$HOME/\~}"
}

main "$@"
