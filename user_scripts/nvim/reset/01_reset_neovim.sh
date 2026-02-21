#!/usr/bin/env bash
# Resets all cache for Neovim

# 1. Safety & Modern Bash settings
set -euo pipefail

# 2. Colors for Orchestra Consistency
RED=$'\033[0;31m'
GREEN=$'\033[0;32m'
BLUE=$'\033[0;34m'
NC=$'\033[0m' # No Color

log_info() { printf "${BLUE}[INFO]${NC} %s\n" "$1"; }
log_success() { printf "${GREEN}[SUCCESS]${NC} %s\n" "$1"; }

# 3. Root Check (Failsafe)
# This script must manipulate User Home files, not Root.
if [[ $EUID -eq 0 ]]; then
    printf "${RED}[ERROR] This script must be run as User, not Root.${NC}\n"
    exit 1
fi

# 4. Target Definition (Associative Array)
# Added "Cache Directory" per user request
declare -A TARGETS=(
    ["Lazy Lockfile"]="${HOME}/.config/nvim/lazy-lock.json"
    ["Data Directory"]="${HOME}/.local/share/nvim"
    ["State Directory"]="${HOME}/.local/state/nvim"
    ["Cache Directory"]="${HOME}/.cache/nvim"
)

# 5. Execution Loop
main() {
    log_info "Starting Neovim state cleanup..."

    for name in "${!TARGETS[@]}"; do
        local path="${TARGETS[$name]}"

        # Check existence first for better logging context
        if [[ -e "$path" ]]; then
            # rm -rf is safe here; it handles both files and directories
            rm -rf "$path"
            log_success "Removed $name: $path"
        else
            log_info "$name not found (Clean): $path"
        fi
    done

    log_success "Neovim state reset complete."
}

main
