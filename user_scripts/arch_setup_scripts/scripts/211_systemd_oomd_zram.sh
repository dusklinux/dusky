#!/usr/bin/env bash
# =============================================================================
# Elite Arch Linux OOM Prevention & Compositor Shielding Configurator
# Target: Arch Linux Cutting-Edge (Kernel 7.1+, Bash 5.3+, systemd 260+)
# Scope: Platinum Grade. High-Performance Userspace OOM Reclaim.
# =============================================================================

set -euo pipefail

readonly SCRIPT_NAME="${0##*/}"
readonly SELF_PATH="$(realpath -e -- "${BASH_SOURCE[0]}")"

# --- Target Configurations ---
readonly EARLYOOM_CONF="/etc/default/earlyoom"
readonly SHIELD_BIN="/usr/local/bin/compositor-oom-shield.sh"
readonly SHIELD_SVC="/etc/systemd/system/compositor-oom-shield.service"

# --- Formatting ---
if [[ -t 1 && -z "${NO_COLOR:-}" ]]; then
    C_RESET=$'\033[0m'
    C_GREEN=$'\033[1;32m'
    C_BLUE=$'\033[1;34m'
    C_RED=$'\033[1;31m'
    C_YELLOW=$'\033[1;33m'
    C_BOLD=$'\033[1m'
else
    C_RESET='' C_GREEN='' C_BLUE='' C_RED='' C_YELLOW='' C_BOLD=''
fi

log_info()    { printf '%s[INFO]%s %s\n'  "$C_BLUE"   "$C_RESET" "$1"; }
log_success() { printf '%s[OK]%s %s\n'    "$C_GREEN"  "$C_RESET" "$1"; }
log_warn()    { printf '%s[WARN]%s %s\n'  "$C_YELLOW" "$C_RESET" "$1"; }
log_error()   { printf '%s[ERROR]%s %s\n' "$C_RED"    "$C_RESET" "$1" >&2; }
die()         { log_error "$1"; exit "${2:-1}"; }

print_help() {
    cat <<EOF
${C_BOLD}Usage:${C_RESET} ${SCRIPT_NAME} [OPTIONS]

  --dry-run, -n        Print the generated configuration and exit without applying
  --help, -h           Show this help menu
EOF
}

usage_error() { log_error "$1"; print_help >&2; exit 2; }

# --- 1. CLI Parsing ---
declare -i DRY_RUN=0

while [[ $# -gt 0 ]]; do
    case "$1" in
        --dry-run|-n)        DRY_RUN=1; shift ;;
        --help|-h)           print_help; exit 0 ;;
        *)                   log_warn "Ignoring unknown argument: $1"; shift ;;
    esac
done

# --- 2. Privilege Escalation ---
if [[ $EUID -ne 0 && $DRY_RUN -eq 0 ]]; then
    log_info "Root privileges required. Escalating..."
    command -v sudo >/dev/null 2>&1 || die "'sudo' is not available."
    exec sudo -- /usr/bin/bash "$SELF_PATH" "$@"
fi

log_info "Initializing Platinum userspace OOM shield optimizer..."

# =============================================================================
# --- 3. CLEANUP GHOST CONFIGURATIONS ---
# =============================================================================
log_info "Scanning for legacy/ghost OOM configurations..."

legacy_configs=(
    "/etc/systemd/oomd.conf.d/99-zram-tuning.conf"
    "/etc/systemd/system/user@.service.d/99-oomd-kill-policy.conf"
    "/etc/systemd/system/user-.slice.d/99-oomd.conf"
    "/etc/systemd/user/session.slice.d/99-oomd-avoid.conf"
    "/etc/systemd/user/scope.d/99-oom-adjust.conf"
    "/etc/systemd/user/wayland-wm@.service.d/99-oomd-avoid.conf"
    "/usr/local/bin/hyprland-oom-shield.sh"
    "/etc/systemd/system/hyprland-oom-shield.service"
)

declare -i CLEANED_ANY=0
for conf in "${legacy_configs[@]}"; do
    if [[ -f "$conf" ]]; then
        # Safety check to avoid deleting a custom user-created script
        if [[ "$conf" == "/usr/local/bin/hyprland-oom-shield.sh" ]]; then
            if ! grep -q "hyprland-oom-shield" "$conf" 2>/dev/null; then
                log_warn "Detected custom user script at ${conf}. Skipping removal to preserve changes."
                continue
            fi
        fi

        if (( DRY_RUN == 0 )); then
            # Handle daemon stopping if active
            if [[ "$conf" == *hyprland-oom-shield.service ]] && systemctl is-active --quiet hyprland-oom-shield.service 2>/dev/null; then
                systemctl disable --now hyprland-oom-shield.service >/dev/null 2>&1 || true
            fi
            rm -f "$conf"
            log_success "Cleaned up legacy file: ${conf}"
        else
            log_info "Would clean up legacy file: ${conf}"
        fi
        CLEANED_ANY=1
    fi
done

if (( CLEANED_ANY == 1 && DRY_RUN == 0 )); then
    log_info "Reloading systemd daemon to ingest cleaned policies..."
    systemctl daemon-reload || log_warn "Global daemon-reload failed. Continuing..."
    
    log_info "Reloading active user managers to unload legacy session configuration..."
    declare -a uids=()
    while read -r line; do
        if [[ "$line" =~ user@([0-9]+)\.service ]]; then
            uids+=("${BASH_REMATCH[1]}")
        fi
    done < <(systemctl list-units --type=service --state=active --plain 'user@*.service' 2>/dev/null || true)

    for uid in "${uids[@]:-}"; do
        user="$(id -un "$uid" 2>/dev/null || true)"
        [[ -z "$user" ]] && continue
        
        # Attempt machinectl reload first, fallback to runuser
        if systemctl --user --machine="${user}@.host" daemon-reload >/dev/null 2>&1; then
            log_success "Reloaded user manager for ${user} (via machinectl)."
        elif command -v runuser >/dev/null 2>&1 && [[ -S "/run/user/${uid}/bus" ]]; then
            if runuser -u "$user" -- env XDG_RUNTIME_DIR="/run/user/${uid}" DBUS_SESSION_BUS_ADDRESS="unix:path=/run/user/${uid}/bus" systemctl --user daemon-reload >/dev/null 2>&1; then
                log_success "Reloaded user manager for ${user} (via runuser)."
            fi
        fi || true 
    done
elif (( CLEANED_ANY == 1 && DRY_RUN == 1 )); then
    log_info "Would run systemctl daemon-reload and reload active user managers for deleted configurations."
fi

# =============================================================================
# --- 4. DISABLE SYSTEMD-OOMD ---
# =============================================================================
log_info "Ensuring systemd-oomd is neutralized to prevent graphical session crashes..."
if (( DRY_RUN == 0 )); then
    systemctl disable --now systemd-oomd.service systemd-oomd.socket >/dev/null 2>&1 || true
    systemctl mask systemd-oomd.service systemd-oomd.socket >/dev/null 2>&1 || true
else
    log_info "Would disable, stop, and mask systemd-oomd.service and systemd-oomd.socket"
fi

# =============================================================================
# --- 5. PACKAGE INSTALLATION AND VALIDATION ---
# =============================================================================
declare -i JUST_INSTALLED=0

if ! command -v earlyoom >/dev/null 2>&1; then
    log_info "earlyoom is not installed. Preparing installation..."
    if (( DRY_RUN == 0 )); then
        # Check for pacman lockfile with a generous 120-second wait limit
        if [[ -f /var/lib/pacman/db.lck ]]; then
            log_warn "Arch package database is locked by another process (/var/lib/pacman/db.lck)."
            log_warn "Waiting up to 120 seconds for lock to release..."
            for _ in {1..120}; do
                sleep 1
                if [[ ! -f /var/lib/pacman/db.lck ]]; then
                    break
                fi
            done
        fi
        
        if [[ -f /var/lib/pacman/db.lck ]]; then
            die "pacman database lock is still active. Please close other package manager tools and rerun."
        fi

        log_info "Installing earlyoom via pacman..."
        systemctl unmask earlyoom.service >/dev/null 2>&1 || true
        
        # Try safe database-native install first. If it fails (due to database mismatch), trigger fallback sync
        if ! pacman -S --needed --noconfirm earlyoom; then
            log_warn "Standard pacman installation failed. Attempting database synchronization..."
            if ! pacman -Sy --needed --noconfirm earlyoom; then
                die "Failed to install earlyoom. Please check your internet connection or run 'pacman -Syu' manually."
            fi
        fi
        JUST_INSTALLED=1
    else
        log_info "Would install package earlyoom via pacman (with fallback database sync if needed)"
    fi
else
    log_info "earlyoom package is already installed."
fi

# =============================================================================
# --- 6. ATOMIC CONFIGURATION FILE GENERATION ---
# =============================================================================
tmp_earlyoom="$(umask 077 && mktemp)"
tmp_shield_bin="$(umask 077 && mktemp)"
tmp_shield_svc="$(umask 077 && mktemp)"
trap 'rm -f "$tmp_earlyoom" "$tmp_shield_bin" "$tmp_shield_svc"' EXIT

# Generate earlyoom configuration payload
# -m 10: Trigger SIGTERM at 10% available memory (SIGKILL at 5%)
# -s 10: Trigger SIGTERM at 10% free swap (SIGKILL at 5%)
# --avoid: Protect compositor (Hyprland, Sway, KWin, Gnome), init, and audio services
cat > "$tmp_earlyoom" <<'EOF'
# Sourced by earlyoom.service
EARLYOOM_ARGS="-m 10 -s 100,100 -r 3600 --avoid '(^|/)(init|systemd.*|Xorg|sshd|Hyprland|sway|kwin_wayland|gnome-shell|wayfire|river|niri|dbus-broker.*|dbus-daemon|pipewire|wireplumber|gnome-keyring.*|xdg-.*|mako|uwsm|start-hyprland|startwayland|hyprland-session|wl-clip-persist|dconf-service|at-spi.*|waitpid|sd-pam|polkitd)$' --prefer '(^|/)(kitty|chrome|firefox|alacritty|discord|slack|electron|obsidian|thunar|gnome-clocks|spotify|code|mpv|vlc|foot|dolphin|gnome-text-editor|nvim|neovim)$'"
EOF

# Generate lightweight compositor shield script
cat > "$tmp_shield_bin" <<'EOF'
#!/usr/bin/env bash
# Managed by 211_systemd_oomd_zram.sh
# Lightweight kernel OOM priority management daemon.
set -euo pipefail

while true; do
    # 1. Shield the compositor
    if pids=$(pgrep -x "Hyprland|sway|kwin_wayland|gnome-shell|wayfire|river|niri" 2>/dev/null); then
        for pid in $pids; do
            if [[ -f "/proc/${pid}/oom_score_adj" ]]; then
                current=$(cat "/proc/${pid}/oom_score_adj" 2>/dev/null || echo "0")
                if [[ "$current" -ne -500 ]]; then
                    echo -500 > "/proc/${pid}/oom_score_adj" 2>/dev/null || true
                fi
            fi
        done
    fi

    # 2. Protect session management, portals, & DBus (set to 0 to prevent kernel OOM choice)
    if pids=$(pgrep -f "uwsm|xdg-desktop-portal|dbus-broker|dbus-daemon" 2>/dev/null); then
        for pid in $pids; do
            if [[ -f "/proc/${pid}/oom_score_adj" ]]; then
                current=$(cat "/proc/${pid}/oom_score_adj" 2>/dev/null || echo "0")
                if [[ "$current" -gt 0 ]]; then
                    echo 0 > "/proc/${pid}/oom_score_adj" 2>/dev/null || true
                fi
            fi
        done
    fi

    # 3. Target user applications in app.slice (set to 800)
    while IFS= read -r -d '' proc_file; do
        if [[ -f "$proc_file" ]]; then
            while read -r pid; do
                if [[ -f "/proc/${pid}/oom_score_adj" ]]; then
                    current=$(cat "/proc/${pid}/oom_score_adj" 2>/dev/null || echo "0")
                    if [[ "$current" -lt 800 ]]; then
                        comm=$(cat "/proc/${pid}/comm" 2>/dev/null || echo "")
                        if [[ ! "$comm" =~ ^(Hyprland|sway|kwin_wayland|gnome-shell|wayfire|river|niri|uwsm|systemd.*|dbus-broker.*|dbus-daemon|pipewire|wireplumber|gnome-keyring.*|xdg-.*|mako|start-hyprland|startwayland|hyprland-session|wl-clip-persist|dconf-service|at-spi.*|waitpid|sd-pam|polkitd)$ ]]; then
                            echo 800 > "/proc/${pid}/oom_score_adj" 2>/dev/null || true
                        fi
                    fi
                fi
            done < "$proc_file"
        fi
    done < <(find /sys/fs/cgroup -path "*app.slice*/cgroup.procs" -print0 2>/dev/null || true)

    sleep 5
done
EOF

# Generate systemd system service for shield
cat > "$tmp_shield_svc" <<'EOF'
[Unit]
Description=Lightweight Compositor Kernel OOM Shield
After=multi-user.target

[Service]
Type=simple
ExecStart=/usr/local/bin/compositor-oom-shield.sh
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
EOF

# Dry run verification of config
if (( DRY_RUN == 1 )); then
    log_info "DRY RUN EXECUTED."
    echo -e "\n${C_BOLD}[ ${EARLYOOM_CONF} ]${C_RESET}"
    cat "$tmp_earlyoom"
    echo -e "\n${C_BOLD}[ ${SHIELD_BIN} ]${C_RESET}"
    cat "$tmp_shield_bin"
    echo -e "\n${C_BOLD}[ ${SHIELD_SVC} ]${C_RESET}"
    cat "$tmp_shield_svc"
    exit 0
fi

# Atomic Installation
declare -i CONFIG_CHANGED=0
declare -i SHIELD_CHANGED=0

install_file() {
    local src="$1" dest="$2" perm="$3"
    local dir
    dir="$(dirname "$dest")"
    if [[ ! -d "$dir" ]]; then
        install -d -m 0755 "$dir"
    fi
    if [[ -f "$dest" ]] && cmp -s "$src" "$dest"; then
        log_info "${dest} is already up to date."
        return 1
    else
        install -m "$perm" "$src" "$dest"
        log_success "Updated ${dest}"
        return 0
    fi
}

# Install EarlyOOM
if install_file "$tmp_earlyoom" "$EARLYOOM_CONF" "0644"; then
    CONFIG_CHANGED=1
fi

# Install Shield Bin & Service
if install_file "$tmp_shield_bin" "$SHIELD_BIN" "0755"; then
    SHIELD_CHANGED=1
fi
if install_file "$tmp_shield_svc" "$SHIELD_SVC" "0644"; then
    SHIELD_CHANGED=1
fi

# =============================================================================
# --- 7. IDEMPOTENT SERVICE LIFECYCLE ---
# =============================================================================
systemctl unmask earlyoom.service >/dev/null 2>&1 || true

# Force restart to ensure active daemon matches the configuration in memory
if true; then
    log_info "Enabling and activating earlyoom service..."
    systemctl enable earlyoom.service >/dev/null 2>&1 || log_warn "Failed to enable earlyoom."
    systemctl restart earlyoom.service >/dev/null 2>&1 || die "Failed to start/restart earlyoom."
    log_success "earlyoom service has been successfully activated and reloaded."
fi

if true; then
    log_info "Enabling and activating compositor-oom-shield service..."
    systemctl daemon-reload >/dev/null 2>&1 || true
    systemctl enable compositor-oom-shield.service >/dev/null 2>&1 || log_warn "Failed to enable compositor-oom-shield."
    systemctl restart compositor-oom-shield.service >/dev/null 2>&1 || die "Failed to start/restart compositor-oom-shield."
    log_success "compositor-oom-shield service has been successfully activated."
fi

log_success "OOM prevention and compositor shielding successfully configured."
exit 0
