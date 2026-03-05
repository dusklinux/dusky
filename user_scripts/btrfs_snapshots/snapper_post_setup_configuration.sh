#!/usr/bin/env bash
# Bash 5.3+ | Configure limine-snapper-sync for Isolated Subvolumes
set -Eeuo pipefail
export LC_ALL=C

# Execute once, print safely, then destroy the trap to prevent cascade noise
trap 'printf "\n\033[1;31m[FATAL]\033[0m Script failed at line %d. Command: %s\n" "$LINENO" "$BASH_COMMAND" >&2; trap - ERR' ERR

AUTO_MODE=false
[[ "${1:-}" == "--auto" ]] && AUTO_MODE=true

sudo -v || { echo "FATAL: Cannot obtain sudo privileges." >&2; exit 1; }
( while true; do sudo -n -v 2>/dev/null; sleep 240; done ) &
SUDO_PID=$!
trap 'kill $SUDO_PID 2>/dev/null || true' EXIT

execute() {
    local desc="$1"
    shift
    if [[ "$AUTO_MODE" == true ]]; then
        "$@"
    else
        printf '\n\033[1;34m[ACTION]\033[0m %s\n' "$desc"
        read -rp "Execute this step? [Y/n] " response || { printf '\nInput closed; aborting.\n' >&2; exit 1; }
        if [[ "${response,,}" =~ ^(n|no)$ ]]; then echo "Skipped."; return 0; fi
        "$@"
    fi
}

configure_sync_daemon() {
    local conf_file="/etc/limine-snapper-sync.conf"
    
    if [[ ! -f "$conf_file" ]]; then
        echo "FATAL: $conf_file not found. Is limine-snapper-sync installed?" >&2
        return 1
    fi

    # Dynamically extract root subvolume name (usually '@')
    local root_subvol
    root_subvol=$(findmnt -fno OPTIONS / | grep -oP 'subvol=/?\K[^,]+' || echo "@")
    
    # We explicitly isolated snapshots to '@snapshots' in Script 2
    local snapshots_subvol="@snapshots"

    # Safely inject the correct isolated paths using sed
    if grep -q "^ROOT_SUBVOLUME_PATH=" "$conf_file"; then
        sudo sed -i "s|^ROOT_SUBVOLUME_PATH=.*|ROOT_SUBVOLUME_PATH=\"/${root_subvol}\"|" "$conf_file"
    else
        echo "ROOT_SUBVOLUME_PATH=\"/${root_subvol}\"" | sudo tee -a "$conf_file" >/dev/null
    fi

    if grep -q "^ROOT_SNAPSHOTS_PATH=" "$conf_file"; then
        sudo sed -i "s|^ROOT_SNAPSHOTS_PATH=.*|ROOT_SNAPSHOTS_PATH=\"/${snapshots_subvol}\"|" "$conf_file"
    else
        echo "ROOT_SNAPSHOTS_PATH=\"/${snapshots_subvol}\"" | sudo tee -a "$conf_file" >/dev/null
    fi
    
    echo "Configuration updated to target isolated subvolumes: /${root_subvol} and /${snapshots_subvol}"
}
execute "Configure limine-snapper-sync paths for top-level subvolumes" configure_sync_daemon

restart_daemon() {
    local unit=""
    if systemctl list-unit-files 'limine-snapper-sync.path' --no-legend | grep -q .; then
        unit="limine-snapper-sync.path"
    elif systemctl list-unit-files 'limine-snapper-sync.service' --no-legend | grep -q .; then
        unit="limine-snapper-sync.service"
    fi
    
    if [[ -n "$unit" ]]; then
        sudo systemctl daemon-reload
        sudo systemctl restart "$unit"
        echo "Daemon ($unit) restarted successfully."
    else
        echo "WARNING: Could not find systemd unit to restart." >&2
    fi
}
execute "Restart limine-snapper-sync daemon to apply changes" restart_daemon

run_sync() {
    echo "Forcing a manual sync to populate Limine boot menu..."
    sudo limine-snapper-sync
}
execute "Populate Limine boot menu with current snapshots" run_sync
