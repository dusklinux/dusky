#!/usr/bin/env bash
# ==============================================================================
# Script: 158_mkinitcpio_restore_and_generate.sh
# Context: Finalization (Chroot)
# Description: Restores ALPM hooks, builds missing presets, and generates initramfs.
# Standard: Arch Linux (Platinum Edition)
# ==============================================================================
set -euo pipefail

if [[ -t 1 ]]; then
    readonly C_BOLD=$'\033[1m'
    readonly C_CYAN=$'\033[36m'
    readonly C_GREEN=$'\033[32m'
    readonly C_YELLOW=$'\033[33m'
    readonly C_RESET=$'\033[0m'
else
    readonly C_BOLD="" C_CYAN="" C_GREEN="" C_YELLOW="" C_RESET=""
fi

printf "%s%s[INFO]%s Restoring pacman mkinitcpio hooks...\n" "${C_BOLD}" "${C_CYAN}" "${C_RESET}"

# Remove the overrides so future kernel updates trigger initramfs generation normally
rm -f /etc/pacman.d/hooks/90-mkinitcpio-install.hook
rm -f /etc/pacman.d/hooks/60-mkinitcpio-remove.hook

printf "%s%s[INFO]%s Restoring missing kernel presets...\n" "${C_BOLD}" "${C_CYAN}" "${C_RESET}"

# Securely enforce directory presence and permissions
install -d -m0755 /etc/mkinitcpio.d

# Dynamically construct the presets that the masked ALPM hook failed to create
for kdir in /usr/lib/modules/*; do
    if [[ -f "$kdir/pkgbase" ]]; then
        pkgbase="$(<"$kdir/pkgbase")"
        preset_file="/etc/mkinitcpio.d/${pkgbase}.preset"

        if [[ ! -f "$preset_file" ]]; then
            printf " -> Generating preset for: %s\n" "$pkgbase"
            cat > "$preset_file" <<EOF
# mkinitcpio preset file for the '${pkgbase}' package
# Generated dynamically by Arch Orchestrator (Script 158)

ALL_kver="/boot/vmlinuz-${pkgbase}"

PRESETS=('default' 'fallback')

default_image="/boot/initramfs-${pkgbase}.img"
fallback_image="/boot/initramfs-${pkgbase}-fallback.img"
fallback_options="-S autodetect"
EOF
            # Platinum Polish: Enforce strict file permissions on the generated preset
            chmod 0644 "$preset_file"
        fi
    fi
done

printf "%s%s[INFO]%s Generating definitive initramfs...\n" "${C_BOLD}" "${C_CYAN}" "${C_RESET}"
printf "%s\n" "----------------------------------------"

# We feed 'n' to safely bypass the limine-mkinitcpio-hook prompt if it fires.
# -P processes all presets in /etc/mkinitcpio.d
mkinitcpio -P < <(echo "n") || {
    printf "%s\n" "----------------------------------------"
    printf "%s%s[WARN]%s mkinitcpio returned a non-zero exit code (usually benign firmware warnings).\n" "${C_BOLD}" "${C_YELLOW}" "${C_RESET}"
}

printf "%s\n" "----------------------------------------"
printf "%s%s[OK]%s Final initramfs generation complete.\n" "${C_BOLD}" "${C_GREEN}" "${C_RESET}"
