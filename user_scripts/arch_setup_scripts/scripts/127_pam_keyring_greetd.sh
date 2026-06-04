#!/usr/bin/env bash
# ==============================================================================
# Arch Linux: Zero-Touch Wayland SSO Master Configuration (Final Revision)
# Target: Hyprland, UWSM, Greetd, Tuigreet, GNOME Keyring, Udiskie
# ==============================================================================

set -euo pipefail

# --- 1. Privilege and Environment Validation ---
if [[ $EUID -ne 0 ]]; then
    echo "This script requires root privileges. Elevating..."
    exec sudo "$0" "$@"
fi

# Accurately identify the human user invoking the script
REAL_USER="${SUDO_USER:-}"
if [[ -z "$REAL_USER" ]] || [[ "$REAL_USER" == "root" ]]; then
    REAL_USER=$(awk -F: '$3 >= 1000 && $3 < 60000 {print $1; exit}' /etc/passwd)
fi
USER_HOME=$(getent passwd "$REAL_USER" | cut -d: -f6)

echo "Targeting user configuration for: $REAL_USER"

# --- 1.5. Root Encryption Detection ---
# Walks the full block-device dependency chain of the root filesystem.
# Returns 0 (true) if ANY layer in that chain is a dm-crypt device.
# Handles simple LUKS, LVM-on-LUKS, and LUKS-on-LVM transparently.
is_root_encrypted() {
    local root_dev
    root_dev=$(findmnt -n -o SOURCE /) || return 1
    lsblk -s -no TYPE "$root_dev" 2>/dev/null | grep -q "^crypt$"
}

# --- 2. mkinitcpio Hook Verification ---
if ! grep -q "^HOOKS=.*systemd.*sd-encrypt" /etc/mkinitcpio.conf; then
    echo "WARNING: /etc/mkinitcpio.conf is missing 'systemd' or 'sd-encrypt' hooks."
    echo "Kernel keyring caching for LUKS SSO will not function without them."
fi

# --- 3. Provision Core Packages ---
echo "Installing core system packages..."
PACKAGES=(
    "greetd" "greetd-tuigreet" "uwsm" "hyprland"
    "gnome-keyring" "libsecret" "seahorse"
    "udisks2" "udiskie" "hyprpolkitagent"
    "git" "base-devel"
)
pacman -S --needed --noconfirm "${PACKAGES[@]}"

# --- 4. Safely Compile AUR PAM Module (LUKS-encrypted root only) ---
if is_root_encrypted; then
    echo "LUKS-encrypted root detected. Resolving 'pam-fde-boot-pw-git' from the AUR..."
    if ! pacman -Qq pam-fde-boot-pw-git &>/dev/null; then
        if command -v paru &>/dev/null; then
            sudo -u "$REAL_USER" paru -S --noconfirm pam-fde-boot-pw-git
        elif command -v yay &>/dev/null; then
            sudo -u "$REAL_USER" yay -S --noconfirm pam-fde-boot-pw-git
        else
            BUILD_DIR=$(sudo -u "$REAL_USER" mktemp -d)
            sudo -u "$REAL_USER" bash -c "cd '$BUILD_DIR' && git clone https://aur.archlinux.org/pam-fde-boot-pw-git.git && cd pam-fde-boot-pw-git && makepkg -si --noconfirm"
            rm -rf "$BUILD_DIR"
        fi
    fi
else
    echo "Root partition is not LUKS-encrypted; skipping 'pam-fde-boot-pw-git'."
fi

# --- 5. Architecting Greetd & Tuigreet ---
echo "Deploying Greetd and Tuigreet cache infrastructure..."

# Explicitly create the cache directory required for --remember to function
mkdir -p /var/cache/tuigreet
chown greeter:greeter /var/cache/tuigreet
chmod 0755 /var/cache/tuigreet

mkdir -p /etc/greetd
cat > /etc/greetd/config.toml << EOF
[terminal]
vt = 1

[default_session]
# Launch Tuigreet with username caching, wrapping the Hyprland desktop entry
command = "tuigreet --time --remember --remember-session --cmd 'uwsm start hyprland.desktop'"
user = "greeter"
EOF
chown -R greeter:greeter /etc/greetd

# --- 6. The Platinum PAM Stack ---
echo "Configuring PAM stack for automated Keyring decryption..."
cp /etc/pam.d/greetd "/etc/pam.d/greetd.bak.$(date +%s)" || true

if is_root_encrypted; then
    # Full SSO stack: LUKS password injected from kernel keyring into GNOME Keyring
    cat > /etc/pam.d/greetd << 'EOF'
#%PAM-1.0
auth       required     pam_securetty.so
auth       requisite    pam_nologin.so
auth       include      system-local-login
account    include      system-local-login

# --- SESSION PHASE ---
session    include      system-local-login
# 1. Extract the LUKS password from the kernel cache
session    optional     pam_fde_boot_pw.so inject_for=gkr
# 2. Start GNOME Keyring Daemon and consume the injected token
session    optional     pam_gnome_keyring.so auto_start
EOF
else
    # Standard stack: GNOME Keyring only; no LUKS injection needed
    cat > /etc/pam.d/greetd << 'EOF'
#%PAM-1.0
auth       required     pam_securetty.so
auth       requisite    pam_nologin.so
auth       include      system-local-login
account    include      system-local-login

# --- SESSION PHASE ---
session    include      system-local-login
# Start GNOME Keyring Daemon
session    optional     pam_gnome_keyring.so auto_start
EOF
fi

# --- 7. Systemd Service Overrides ---
echo "Applying Systemd overrides for Kernel Keyring inheritance..."
mkdir -p /etc/systemd/system/greetd.service.d
cat > /etc/systemd/system/greetd.service.d/keyringmode.conf << 'EOF'
[Service]
KeyringMode=inherit
EOF

# --- 8. Automating Udiskie for External Drives ---
echo "Writing udiskie YAML configuration..."
mkdir -p "${USER_HOME}/.config/udiskie"
cat > "${USER_HOME}/.config/udiskie/config.yml" << 'EOF'
program_options:
  # Native libsecret integration to fetch passwords invisibly
  password_prompt: "builtin:gui"
  automount: true
  notify: true
  tray: auto
EOF
chown -R "$REAL_USER":"$REAL_USER" "${USER_HOME}/.config/udiskie"

# --- 9. Service Enablement ---
echo "Enabling boot services..."
if [[ -d /run/systemd/system ]]; then
    systemctl daemon-reload
    systemctl enable greetd.service
else
    # Fallback if executing inside arch-chroot during installation
    ln -sf /usr/lib/systemd/system/greetd.service /etc/systemd/system/display-manager.service
fi

echo "====================================================================="
echo " Deployment Complete! You are fully configured for Wayland SSO.      "
echo "====================================================================="
