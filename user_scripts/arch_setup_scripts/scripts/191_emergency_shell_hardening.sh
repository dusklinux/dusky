#!/usr/bin/env bash
# Arch Linux | Systemd Emergency & Rescue Console Hardening
# Prevents sulogin "Cannot open access to console, the root account is locked" lockout in systemd and initramfs.

set -Eeuo pipefail
export LC_ALL=C

ensure_root() {
    if (( EUID != 0 )); then
        printf '\033[1;32m[*]\033[0m Elevating to root privileges via sudo...\n'
        exec sudo -- "$0" "$@"
    fi
}

fatal() { printf '\033[1;31m[FATAL]\033[0m %s\n' "$1" >&2; exit 1; }
info() { printf '\033[1;32m[INFO]\033[0m %s\n' "$1"; }

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

    # Update bootloader kernel parameters (systemd-boot, GRUB, Limine)
    if [ -d /boot/loader/entries ]; then
        info "Updating systemd-boot entries with SYSTEMD_SULOGIN_FORCE=1..."
        for conf in /boot/loader/entries/*.conf; do
            [[ -f "$conf" ]] || continue
            if ! grep -q "SYSTEMD_SULOGIN_FORCE=1" "$conf"; then
                sed -i 's/options /options systemd.setenv=SYSTEMD_SULOGIN_FORCE=1 rd.systemd.setenv=SYSTEMD_SULOGIN_FORCE=1 /' "$conf"
            fi
        done
    fi

    if [ -f /etc/default/grub ]; then
        info "Updating GRUB parameters with SYSTEMD_SULOGIN_FORCE=1..."
        if ! grep -q "SYSTEMD_SULOGIN_FORCE=1" /etc/default/grub; then
            sed -i 's/GRUB_CMDLINE_LINUX_DEFAULT="/GRUB_CMDLINE_LINUX_DEFAULT="systemd.setenv=SYSTEMD_SULOGIN_FORCE=1 rd.systemd.setenv=SYSTEMD_SULOGIN_FORCE=1 /' /etc/default/grub
            if command -v grub-mkconfig >/dev/null 2>&1; then
                grub-mkconfig -o /boot/grub/grub.cfg 2>/dev/null || true
            fi
        fi
    fi

    # Update initramfs to embed overrides into early initrd stage
    if [ -f /etc/mkinitcpio.conf ]; then
        info "Embedding emergency overrides into initramfs via mkinitcpio..."
        if ! grep -q "emergency.service.d" /etc/mkinitcpio.conf; then
            sed -i "s|^FILES=(\(.*\))|FILES=(\1 /etc/systemd/system/emergency.service.d/override.conf /etc/systemd/system/rescue.service.d/override.conf /etc/shadow)|g" /etc/mkinitcpio.conf
        fi
        if command -v mkinitcpio >/dev/null 2>&1; then
            mkinitcpio -P
        fi
    elif command -v dracut >/dev/null 2>&1; then
        info "Regenerating initramfs via dracut..."
        dracut --regenerate-all --force
    fi

    info "Systemd & initramfs emergency console hardening successfully applied!"
}

ensure_root
apply_emergency_hardening
