#!/usr/bin/env bash
# =============================================================================
# Elite Arch Linux OOM Prevention & Compositor Shielding Configurator
# Target: Arch Linux Cutting-Edge (Kernel 7.1+, Bash 5.3+, systemd 261+)
# Scope: Platinum Grade. High-Performance Userspace OOM Reclaim.
# =============================================================================

set -euo pipefail

readonly SCRIPT_NAME="${0##*/}"
SELF_PATH="$(realpath -e -- "${BASH_SOURCE[0]}")"
readonly SELF_PATH

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
    cat <<HELP
${C_BOLD}Usage:${C_RESET} ${SCRIPT_NAME} [OPTIONS]

  --dry-run, -n        Print the generated configuration and exit without applying
  --help, -h           Show this help menu
HELP
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

log_info "Initializing Platinum systemd-oomd 261 optimizer..."

# =============================================================================
# --- 3. SYSTEMD-OOMD CONFIGURATION (SYSTEMD 261+) ---
# =============================================================================
log_info "Configuring native systemd-oomd rules for ZRAM and UWSM isolation..."

tmp_oomrule_pressure="$(umask 077 && mktemp)"
tmp_oomrule_swap="$(umask 077 && mktemp)"
tmp_app_slice="$(umask 077 && mktemp)"
tmp_session_slice="$(umask 077 && mktemp)"
tmp_wayland_wm="$(umask 077 && mktemp)"
trap 'rm -f "$tmp_oomrule_pressure" "$tmp_oomrule_swap" "$tmp_app_slice" "$tmp_session_slice" "$tmp_wayland_wm"' EXIT

# --- ZRAM-aware OOM rulesets (systemd 261 OOMRules=, see oomd.conf(5)) ---
#
# Two SEPARATE rulesets are used on purpose. Within a single .oomrule file,
# MemoryPressureAbove= and SwapUsageMax= are combined with AND, which is a
# trap on a zram-only system: zram's compression lets reported swap usage
# stay well under any sane threshold even while physical RAM is fully
# exhausted and the system is thrashing, so a single rule requiring both
# conditions could simply never fire. Listing multiple rulesets in
# OOMRules= evaluates them independently (effectively OR), so either
# condition alone is enough to trigger its own action.

# Primary trigger: PSI memory pressure. This is the zram-proof signal,
# since it measures stall time, not raw memory/swap fullness.
cat > "$tmp_oomrule_pressure" <<'OOMRULE_PRESSURE'
[Rule]
MemoryPressureAbove=10%
Action=kill-by-pgscan
LastingSec=2s
OOMRULE_PRESSURE

# Backstop: catastrophic zram (swap) saturation, independent of pressure.
cat > "$tmp_oomrule_swap" <<'OOMRULE_SWAP'
[Rule]
SwapUsageMax=90%
Action=kill-by-swap
LastingSec=2s
OOMRULE_SWAP

# Bind to user applications via app-graphical.slice (UWSM app target).
# Both ManagedOOMMemoryPressure=kill and ManagedOOMSwap=kill are set below.
# Per systemd-oomd.service(8): "Cgroups of units with ManagedOOMSwap= or
# ManagedOOMMemoryPressure= set to kill will be monitored" -- either alone
# is documented as sufficient to arm monitoring (and, by extension, OOMRules=
# evaluation) for this cgroup. Setting BOTH is not required by that reading,
# but the upstream .oomrule documentation never states outright that a
# custom SwapUsageMax= rule specifically requires ManagedOOMSwap=kill (as
# opposed to ManagedOOMMemoryPressure=kill) to be armed. Since setting both
# costs nothing and closes that gray area entirely, both are set explicitly
# rather than betting the swap backstop rule's correctness on an inference.
cat > "$tmp_app_slice" <<'APPSLICE'
[Slice]
ManagedOOMMemoryPressure=kill
ManagedOOMSwap=kill
OOMRules=10-zram-desktop-pressure 10-zram-desktop-swap
APPSLICE

# Protect the graphical session slice
cat > "$tmp_session_slice" <<'SESSIONSLICE'
[Slice]
ManagedOOMPreference=avoid
SESSIONSLICE

# =============================================================================
# --- 4. SYSTEM-LEVEL OOM SCORE INHERITANCE FIX ---
# =============================================================================
# systemd --user cannot set a child's OOMScoreAdjust lower than its own.
# By default, user@.service has OOMScoreAdjust=100.
# We must set user@.service to -500 so it has privileges to spawn critical daemons at -500.
# We simultaneously set DefaultOOMScoreAdjust=200 so normal user apps don't inherit -500.

tmp_user_service="$(umask 077 && mktemp)"
tmp_user_conf="$(umask 077 && mktemp)"
trap 'rm -f "$tmp_oomrule_pressure" "$tmp_oomrule_swap" "$tmp_app_slice" "$tmp_session_slice" "$tmp_wayland_wm" "$tmp_user_service" "$tmp_user_conf"' EXIT

cat > "$tmp_user_service" <<'USERSERVICE'
[Service]
OOMScoreAdjust=-500
USERSERVICE

cat > "$tmp_user_conf" <<'USERCONF'
[Manager]
DefaultOOMScoreAdjust=200
DefaultEnvironment="UWSM_APP_UNIT_TYPE=service"
USERCONF

# Failsafe: Protect critical session components from kernel OOM killer
# and prevent systemd from killing them if a child dies
cat > "$tmp_wayland_wm" <<'WAYLANDWM'
[Service]
OOMScoreAdjust=-500
OOMPolicy=continue
WAYLANDWM

critical_services=(
    "wayland-wm@.service"
    "wayland-wm-app-daemon.service"
    "wayland-wm-env@.service"
    "wayland-session-bindpid@.service"
    "wireplumber.service"
    "pipewire.service"
    "pipewire-pulse.service"
    "xdg-desktop-portal.service"
    "xdg-desktop-portal-gtk.service"
    "xdg-desktop-portal-hyprland.service"
    "dbus-broker.service"
    "dbus.service"
    "mako.service"
)

system_critical_services=(
    "systemd-logind.service"
    "NetworkManager.service"
    "polkit.service"
    "systemd-resolved.service"
    "systemd-timesyncd.service"
    "getty@.service"
    "udisks2.service"
    "systemd-userdbd.service"
)

if (( DRY_RUN == 1 )); then
    log_info "DRY RUN EXECUTED."
    echo -e "\n${C_BOLD}[ /etc/systemd/oomd/rules.d/10-zram-desktop-pressure.oomrule ]${C_RESET}"
    cat "$tmp_oomrule_pressure"
    echo -e "\n${C_BOLD}[ /etc/systemd/oomd/rules.d/10-zram-desktop-swap.oomrule ]${C_RESET}"
    cat "$tmp_oomrule_swap"
    echo -e "\n${C_BOLD}[ /etc/systemd/user/app-graphical.slice.d/10-oomd.conf ]${C_RESET}"
    cat "$tmp_app_slice"
    echo -e "\n${C_BOLD}[ /etc/systemd/user/session-graphical.slice.d/10-oomd-avoid.conf ]${C_RESET}"
    cat "$tmp_session_slice"
    
    echo -e "\n${C_BOLD}[ Shield applied to critical user services: ]${C_RESET}"
    for svc in "${critical_services[@]}"; do
        echo "/etc/systemd/user/${svc}.d/10-oom-shield.conf"
    done
    echo -e "\n${C_BOLD}[ Shield applied to critical system services: ]${C_RESET}"
    for svc in "${system_critical_services[@]}"; do
        echo "/etc/systemd/system/${svc}.d/10-oom-shield.conf"
    done
    cat "$tmp_wayland_wm"
    exit 0
fi

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

install_file "$tmp_oomrule_pressure" "/etc/systemd/oomd/rules.d/10-zram-desktop-pressure.oomrule" "0644" || true
install_file "$tmp_oomrule_swap" "/etc/systemd/oomd/rules.d/10-zram-desktop-swap.oomrule" "0644" || true
install_file "$tmp_app_slice" "/etc/systemd/user/app-graphical.slice.d/10-oomd.conf" "0644" || true
install_file "$tmp_session_slice" "/etc/systemd/user/session-graphical.slice.d/10-oomd-avoid.conf" "0644" || true
install_file "$tmp_user_service" "/etc/systemd/system/user@.service.d/10-oom-score.conf" "0644" || true
install_file "$tmp_user_conf" "/etc/systemd/user.conf.d/10-oom-default.conf" "0644" || true

for svc in "${critical_services[@]}"; do
    install_file "$tmp_wayland_wm" "/etc/systemd/user/${svc}.d/10-oom-shield.conf" "0644" || true
done

for svc in "${system_critical_services[@]}"; do
    install_file "$tmp_wayland_wm" "/etc/systemd/system/${svc}.d/10-oom-shield.conf" "0644" || true
done

# =============================================================================
# --- 5. ENABLE AND START NATIVE SYSTEMD-OOMD ---
# =============================================================================
log_info "Restoring and activating native systemd-oomd service..."
systemctl unmask systemd-oomd.service systemd-oomd.socket >/dev/null 2>&1 || true
systemctl enable systemd-oomd.service systemd-oomd.socket >/dev/null 2>&1 || log_warn "Failed to enable systemd-oomd."
systemctl restart systemd-oomd.service systemd-oomd.socket >/dev/null 2>&1 || log_warn "Failed to restart systemd-oomd."

log_info "Reloading systemd daemon to ingest OOM policies..."
systemctl daemon-reload >/dev/null 2>&1 || true

log_info "Disabling UWSM unit failure monitor (fumon.service) globally..."
systemctl --global disable fumon.service >/dev/null 2>&1 || true

log_info "Reloading active user managers..."
declare -a uids=()
while read -r line; do
    if [[ "$line" =~ user@([0-9]+)\.service ]]; then
        uids+=("${BASH_REMATCH[1]}")
    fi
done < <(systemctl list-units --type=service --state=active --plain 'user@*.service' 2>/dev/null || true)

for uid in "${uids[@]:-}"; do
    user="$(id -un "$uid" 2>/dev/null || true)"
    [[ -z "$user" ]] && continue
    # Disable active fumon service for current user sessions to apply changes immediately
    systemctl --user -M "${user}@" disable --now fumon.service >/dev/null 2>&1 || true
    if systemctl --user -M "${user}@" daemon-reload >/dev/null 2>&1; then
        log_success "Reloaded user manager for ${user}."
    fi
done

log_success "Platinum OOM architecture successfully deployed via systemd 261."
exit 0
