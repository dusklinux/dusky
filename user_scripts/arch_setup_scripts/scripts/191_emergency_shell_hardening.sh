#!/usr/bin/env bash
# Arch Linux | Systemd Emergency & Rescue Console Hardening
# Prevents sulogin "Cannot open access to console, the root account is locked" lockout.

set -Eeuo pipefail
export LC_ALL=C

preflight_checks() {
    (( EUID == 0 )) || fatal "Run as root."
}

fatal() { printf '\033[1;31m[FATAL]\033[0m %s\n' "$1" >&2; exit 1; }
info() { printf '\033[1;32m[INFO]\033[0m %s\n'; }

apply_emergency_hardening() {
    info "Configuring Systemd Emergency and Rescue shell overrides..."

    mkdir -p /etc/systemd/system/emergency.service.d
    mkdir -p /etc/systemd/system/rescue.service.d

    cat << 'EOF' > /etc/systemd/system/emergency.service.d/override.conf
[Service]
Environment=SYSTEMD_SULOGIN_FORCE=1
EOF

    cat << 'EOF' > /etc/systemd/system/rescue.service.d/override.conf
[Service]
Environment=SYSTEMD_SULOGIN_FORCE=1
EOF

    systemctl daemon-reload
    info "Systemd emergency console hardening successfully applied."
}

preflight_checks
apply_emergency_hardening
