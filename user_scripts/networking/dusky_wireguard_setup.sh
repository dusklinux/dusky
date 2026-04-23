#!/usr/bin/env bash
# ==============================================================================
# dusky_wireguard_setup.sh
# ==============================================================================
# Run once to configure the system for the Dusky WireGuard CC integration:
#   0. Installs wireguard-tools via pacman if not present
#   1. Creates /etc/wireguard/ if absent
#   2. Sets ownership/permissions: root:wheel 750 (wheel can list, not read keys)
#   3. Installs /etc/sudoers.d/dusky-wireguard granting NOPASSWD for wg-quick
# ==============================================================================
set -euo pipefail

readonly CLR_GRN=$'\e[1;32m'
readonly CLR_YLW=$'\e[1;33m'
readonly CLR_RED=$'\e[1;31m'
readonly CLR_BLU=$'\e[1;34m'
readonly CLR_RST=$'\e[0m'

ok()   { printf '%s[OK]%s    %s\n' "$CLR_GRN" "$CLR_RST" "$1"; }
info() { printf '%s[INFO]%s  %s\n' "$CLR_BLU" "$CLR_RST" "$1"; }
warn() { printf '%s[WARN]%s  %s\n' "$CLR_YLW" "$CLR_RST" "$1"; }
err()  { printf '%s[ERR]%s   %s\n' "$CLR_RED" "$CLR_RST" "$1" >&2; }

CURRENT_USER="${SUDO_USER:-$USER}"

# ── 0. Ensure wireguard-tools is installed ────────────────────────────────────
if command -v wg &>/dev/null; then
    ok "wireguard-tools already installed ($(wg --version 2>/dev/null || echo wg found))"
else
    info "wireguard-tools not found — installing via pacman ..."
    if sudo pacman -S --noconfirm wireguard-tools; then
        ok "wireguard-tools installed"
    else
        err "Failed to install wireguard-tools — aborting"
        exit 1
    fi
fi

# ── 1. Ensure /etc/wireguard exists ──────────────────────────────────────────
if [[ ! -d /etc/wireguard ]]; then
    info "Creating /etc/wireguard ..."
    sudo mkdir -p /etc/wireguard
    ok "Directory created"
else
    info "/etc/wireguard already exists"
fi

# ── 2. Set permissions: root:wheel 750 ───────────────────────────────────────
# 750 = root can rwx, wheel can r-x (list + traverse), others nothing.
# Individual .conf files stay at 600 root:root — private keys never world-readable.
info "Setting /etc/wireguard ownership to root:wheel, mode 750 ..."
sudo chown root:wheel /etc/wireguard
sudo chmod 750 /etc/wireguard
ok "Directory permissions set (root:wheel 750)"

# ── 3. Lock down any existing configs ────────────────────────────────────────
shopt -s nullglob
confs=(/etc/wireguard/*.conf)
if (( ${#confs[@]} > 0 )); then
    info "Securing existing configs (root:root 600) ..."
    for conf in "${confs[@]}"; do
        sudo chown root:root "$conf"
        sudo chmod 600 "$conf"
        ok "Secured: $conf"
    done
else
    info "No existing configs to secure"
fi

# ── 4. Install sudoers rules ──────────────────────────────────────────────────
SUDOERS_FILE="/etc/sudoers.d/dusky-wireguard"
info "Installing sudoers rules to $SUDOERS_FILE ..."

sudo tee "$SUDOERS_FILE" > /dev/null <<EOF
# Dusky WireGuard CC integration — allow wheel members to manage tunnels
# systemd-style (wg-quick@<name>)
%wheel ALL=(ALL) NOPASSWD: /usr/bin/systemctl start wg-quick@*
%wheel ALL=(ALL) NOPASSWD: /usr/bin/systemctl stop wg-quick@*
%wheel ALL=(ALL) NOPASSWD: /usr/bin/systemctl restart wg-quick@*
# direct wg-quick (also matches path-based: wg-quick up /etc/wireguard/subdir/name.conf)
%wheel ALL=(ALL) NOPASSWD: /usr/bin/wg-quick up *
%wheel ALL=(ALL) NOPASSWD: /usr/bin/wg-quick down *
# wg show (live status + diagnostics)
%wheel ALL=(ALL) NOPASSWD: /usr/bin/wg show
%wheel ALL=(ALL) NOPASSWD: /usr/bin/wg show *
EOF

sudo chmod 440 "$SUDOERS_FILE"

# Validate — visudo -c will catch syntax errors before they lock you out
if sudo visudo -c -f "$SUDOERS_FILE" &>/dev/null; then
    ok "Sudoers rules installed and validated"
else
    err "Sudoers syntax check failed — removing $SUDOERS_FILE to prevent lockout"
    sudo rm -f "$SUDOERS_FILE"
    exit 1
fi

# ── 5. Summary ───────────────────────────────────────────────────────────────
echo
printf '%s══ Setup Complete ══%s\n' "$CLR_GRN" "$CLR_RST"
echo "  /etc/wireguard/       → root:wheel 750"
echo "  *.conf files          → root:root  600"
echo "  $SUDOERS_FILE → NOPASSWD for wg-quick, wg show"
echo
info "You can now add tunnels via the Dusky Control Center → WireGuard"
echo
printf 'Press Enter to close...'
read -r
