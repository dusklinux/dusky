#!/usr/bin/env bash
# Reset generated Neovim data while retaining configuration and its lockfile.
set -euo pipefail
main() {
    local manager
    manager="$(dirname -- "${BASH_SOURCE[0]}")/../dusky_neovim_manager.sh"
    # shellcheck source=user_scripts/nvim/dusky_neovim_manager.sh
    source "$manager"
    initialize || return 1
    reset_neovim_state
}
if [[ "${BASH_SOURCE[0]}" == "$0" ]]; then
    main "$@"
fi
