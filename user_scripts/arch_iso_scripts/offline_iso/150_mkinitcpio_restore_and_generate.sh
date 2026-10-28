#!/usr/bin/env bash
# ==============================================================================
# Script: 150_mkinitcpio_restore_and_generate.sh
# Context: Finalization (Chroot)
# Description: Restores ALPM hooks, builds missing presets, and generates initramfs.
# Standard: Arch Linux (Platinum Edition)
# ==============================================================================
set -euo pipefail

if [[ -t 1 ]]; then
    readonly C_BOLD=$'\033[1m'
    readonly C_CYAN=$'\033[36m'
    readonly C_GREEN=$'\033[32m'
    readonly C_RED=$'\033[31m'
    readonly C_RESET=$'\033[0m'
else
    readonly C_BOLD="" C_CYAN="" C_GREEN="" C_RED="" C_RESET=""
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
# Generated dynamically by Arch Orchestrator (Script 150)

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

printf "%s%s[INFO]%s Staging kernels from /usr/lib/modules to /boot...\n" "${C_BOLD}" "${C_CYAN}" "${C_RESET}"

# The masked ALPM hook (070) never staged vmlinuz; stage it now so preset ALL_kver resolves.
found_kernel=0
declare -a required_images=()
for kdir in /usr/lib/modules/*; do
    if [[ -f "$kdir/vmlinuz" ]] && [[ -f "$kdir/pkgbase" ]]; then
        pkgbase="$(<"$kdir/pkgbase")"
        install -m0644 "$kdir/vmlinuz" "/boot/vmlinuz-${pkgbase}"
        required_images+=("/boot/initramfs-${pkgbase}.img")
        found_kernel=1
    fi
done

if [[ "$found_kernel" -eq 0 ]]; then
    printf "%s%s[ERROR]%s No kernels found in /usr/lib/modules!\n" "${C_BOLD}" "${C_RED}" "${C_RESET}"
    exit 1
fi

printf "%s%s[INFO]%s Generating definitive initramfs...\n" "${C_BOLD}" "${C_CYAN}" "${C_RESET}"
printf "%s\n" "----------------------------------------"

# We feed 'n' to safely bypass the limine-mkinitcpio-hook prompt if it fires.
# -P processes all presets in /etc/mkinitcpio.d
mkinitcpio_exit=0
mkinitcpio -P < <(echo "n") || mkinitcpio_exit=$?

printf "%s\n" "----------------------------------------"
if [[ "$mkinitcpio_exit" -ne 0 ]]; then
    printf "%s%s[ERROR]%s mkinitcpio failed with status %d; existing images do not prove this build succeeded.\n" "${C_BOLD}" "${C_RED}" "${C_RESET}" "$mkinitcpio_exit" >&2
    exit "$mkinitcpio_exit"
fi

# The bootloader uses each kernel's main image; a fallback alone is insufficient.
for img in "${required_images[@]}"; do
    if [[ ! -s "$img" ]]; then
        printf "%s%s[ERROR]%s Required initramfs is empty or missing: %s\n" "${C_BOLD}" "${C_RED}" "${C_RESET}" "$img"
        exit 1
    fi
done

# Verify all generated images, including any fallback images.
valid_images=0
invalid_images=0
shopt -s nullglob
for img in /boot/initramfs-*.img; do
    if [[ -s "$img" ]]; then
        valid_images=$((valid_images + 1))
        printf " -> Verified initramfs: %s (%s)\n" "$img" "$(du -h "$img" | cut -f1)"
    else
        invalid_images=$((invalid_images + 1))
        printf "%s%s[ERROR]%s Generated initramfs is empty or missing: %s\n" "${C_BOLD}" "${C_RED}" "${C_RESET}" "$img"
    fi
done
shopt -u nullglob

if (( valid_images == 0 || invalid_images > 0 )); then
    printf "%s%s[ERROR]%s Initramfs verification failed in /boot! Check disk space or preset configuration.\n" "${C_BOLD}" "${C_RED}" "${C_RESET}"
    exit 1
fi

printf "%s%s[OK]%s Final initramfs generation complete (%d image(s) verified).\n" "${C_BOLD}" "${C_GREEN}" "${C_RESET}" "$valid_images"
