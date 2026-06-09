#!/usr/bin/env bash
# ==============================================================================
# Script: 005_uefi_check.sh
# Description: Strictly validates UEFI boot environment for modern Arch Linux.
#              Fails gracefully with an interactive override mechanism.
# Target: Modern Linux Kernels (6.x / 7.x+)
# ==============================================================================

set -euo pipefail

# --- UEFI Detection Engine ---
is_uefi_booted() {
    # Method 1: The absolute source of truth on modern kernels.
    # If this directory exists, the kernel was booted via EFI stub or bootloader.
    if [[ -d /sys/firmware/efi ]]; then return 0; fi
    
    # Method 2: Platform size file (Introduced in newer kernels as the definitive architecture check).
    if [[ -f /sys/firmware/efi/fw_platform_size ]]; then return 0; fi
    
    # Method 3: Live filesystem mount check.
    # Sometimes /sys/firmware/efi is missing if efivarfs was manually mounted elsewhere,
    # though highly unlikely on the official Arch ISO.
    if grep -qsw 'efivarfs' /proc/mounts 2>/dev/null; then return 0; fi
    
    # Method 4: Kernel ring buffer (dmesg).
    # Catches edge cases where sysfs isn't populated but EFI initialization occurred.
    # Using 2>/dev/null to prevent dmesg_restrict permission errors from bleeding into stdout.
    if dmesg 2>/dev/null | grep -qiE 'efi:.*v[0-9]'; then return 0; fi
    
    return 1
}

# -> If UEFI is detected, exit instantly and silently.
if is_uefi_booted; then
    exit 0
fi

# ==============================================================================
# BIOS / Legacy Mode Detected - Interactive UX
# ==============================================================================

readonly C_RED=$'\033[1;31m'
readonly C_YELLOW=$'\033[1;33m'
readonly C_BLUE=$'\033[1;34m'
readonly C_BOLD=$'\033[1m'
readonly C_RESET=$'\033[0m'
readonly BEEP=$'\a'

printf "\n%s%s=== CRITICAL BOOT WARNING ===%s%s\n" "$BEEP" "${C_RED}${C_BOLD}" "$C_RESET"
printf "%sYour system is currently booted in Legacy BIOS (CSM) mode.%s\n\n" "$C_YELLOW" "$C_RESET"

printf "This Arch Linux installation strictly relies on %s%ssystemd-boot%s.\n" "$C_BOLD" "$C_BLUE" "$C_RESET"
printf "Because systemd-boot requires direct access to motherboard NVRAM,\n"
printf "it %sSTRICTLY REQUIRES%s a UEFI environment to function.\n\n" "${C_BOLD}${C_RED}" "$C_RESET"

printf "If you continue in BIOS mode, the base installation will complete,\n"
printf "but the bootloader phase will %sFAIL%s, leaving your system %sUNBOOTABLE%s.\n\n" "$C_BOLD" "$C_RESET" "${C_BOLD}${C_RED}" "$C_RESET"

printf "%sRECOMMENDED ACTION:%s\n" "$C_BOLD" "$C_RESET"
printf "  1. Abort this installation now.\n"
printf "  2. Reboot your computer or virtual machine.\n"
printf "  3. Enter your BIOS / Firmware settings.\n"
printf "  4. Disable 'CSM' or 'Legacy Boot' and enable 'UEFI Boot'.\n"
printf "  5. Boot this USB drive again.\n\n"

# Safety constraint: Prevent hanging indefinitely if Orchestrator is running autonomously (-a)
if [[ "${AUTO_MODE:-0}" == "1" ]]; then
    printf "%s[ERR]%s Auto-Mode (--auto) is enabled. Aborting installation to prevent a doomed system state.\n" "$C_RED" "$C_RESET"
    printf "Please fix your firmware to boot in UEFI mode, or run the script interactively to override this.\n\n"
    exit 1
fi

# Interactive Prompt to allow user override
while true; do
    read -r -p "Do you understand and want to continue anyway? (Not recommended) [y/N]: " choice
    case "${choice,,}" in
        y|yes)
            printf "\n%s[WARN]%s Proceeding in BIOS mode at your request. Bootloader failure is imminent.\n" "$C_YELLOW" "$C_RESET"
            exit 0 # Exiting 0 tells the orchestrator to continue normally
            ;;
        n|no|"")
            printf "\n%s[INFO]%s Wise choice. Installation aborted. Please reboot into UEFI mode.\n" "$C_BLUE" "$C_RESET"
            exit 1 # Exiting 1 tells the orchestrator to halt
            ;;
        *)
            printf "Please answer 'y' (yes) or 'n' (no).\n"
            ;;
    esac
done
