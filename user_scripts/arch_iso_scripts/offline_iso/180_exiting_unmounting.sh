#!/usr/bin/env bash
# -----------------------------------------------------------------------------
# Script Name: 05-finish-install.sh
# Description: Final instruction set for Arch Linux installation.
#              Displays critical post-install manual steps and can arm
#              autonomous finish mode for the live ISO caller by writing a flag
#              file inside the installed system.
# -----------------------------------------------------------------------------

set -euo pipefail

readonly SCRIPT_NAME="${0##*/}"
# Written inside the chroot; visible from the live ISO as:
# /mnt/root/.arch-installer-finish-auto
readonly AUTO_FLAG_FILE="/root/.arch-installer-finish-auto"

AUTO_MODE=0

# Color defaults (disabled automatically when stdout is not a TTY)
C_RESET=''
C_BOLD=''
C_GREEN=''
C_CYAN=''
C_YELLOW=''
C_RED=''
C_WHITE=''

usage() {
    cat <<EOF
Usage: ${SCRIPT_NAME} [--auto|-a] [--help|-h]

Options:
  -a, --auto   Arm autonomous finish mode.
  -h, --help   Show this help text.

Notes:
  - This script is intended to run inside 'arch-chroot /mnt'.
  - It does not itself run 'umount -R /mnt' or 'poweroff'.
  - In auto mode, it writes a flag file for the live ISO caller to detect
    after the chrooted command returns.
  - If you launched an interactive 'arch-chroot /mnt' shell manually, you
    still must type 'exit' once yourself after this script finishes.
EOF
}

parse_args() {
    while (($#)); do
        case "$1" in
            -a|--auto)
                AUTO_MODE=1
                ;;
            -h|--help)
                usage
                exit 0
                ;;
            *)
                printf 'Error: unknown option: %s\n\n' "$1" >&2
                usage >&2
                exit 1
                ;;
        esac
        shift
    done
}

setup_terminal() {
    if [[ -t 1 ]]; then
        # If TERM is unusable in the chroot, fall back to something generic.
        if command -v tput >/dev/null 2>&1 && ! tput longname >/dev/null 2>&1; then
            export TERM=xterm
        fi

        C_RESET=$'\033[0m'
        C_BOLD=$'\033[1m'
        C_GREEN=$'\033[1;32m'
        C_CYAN=$'\033[1;36m'
        C_YELLOW=$'\033[1;33m'
        C_RED=$'\033[1;31m'
        C_WHITE=$'\033[1;37m'
    fi
}

clear_screen() {
    if [[ -t 1 ]] && command -v clear >/dev/null 2>&1; then
        clear || true
    fi
}

print_banner() {
    printf "\n%b%s%b\n" "${C_GREEN}${C_BOLD}" "========================================" "${C_RESET}"
    printf "%b%s%b\n"   "${C_GREEN}${C_BOLD}" "   ARCH LINUX INSTALLATION COMPLETE     " "${C_RESET}"
    printf "%b%s%b\n"   "${C_GREEN}${C_BOLD}" "========================================" "${C_RESET}"
}

print_step() {
    local step_num="$1"
    local cmd="$2"
    local desc="$3"

    printf "\n%b[Step %s]%b %s\n" "${C_CYAN}" "${step_num}" "${C_RESET}" "${desc}"
    printf "   %b$ %b%s%b\n" "${C_WHITE}" "${C_YELLOW}${C_BOLD}" "${cmd}" "${C_RESET}"
}

print_warning() {
    printf "\n%b[!] CRITICAL WARNING:%b %s\n" "${C_RED}${C_BOLD}" "${C_RESET}" "$1"
}

prompt_for_auto_mode() {
    local reply=''

    # Only prompt in an interactive session, and only if --auto was not given.
    if (( AUTO_MODE == 0 )) && [[ -t 0 && -t 1 ]]; then
        printf "\nEnable autonomous finish mode? [y/N]: "
        IFS= read -r reply || true

        case "$reply" in
            [Yy]|[Yy][Ee][Ss])
                AUTO_MODE=1
                ;;
        esac
    fi
}

update_auto_flag() {
    if (( AUTO_MODE )); then
        # Restrictive permissions are sufficient; the caller only needs to see
        # that the file exists under /mnt/root/.
        ( umask 077 && printf '%s\n' 'AUTO_FINISH=1' > "${AUTO_FLAG_FILE}" )
    else
        rm -f -- "${AUTO_FLAG_FILE}"
    fi
}

show_manual_mode() {
    printf "Please perform the following steps %bMANUALLY%b to ensure filesystem integrity.\n" "${C_BOLD}" "${C_RESET}"

    print_step "1" "exit" "Return to the live ISO environment."
    printf "   %b(Note: if you entered with 'arch-chroot /mnt', typing 'exit' once leaves the chroot shell.)%b\n" "${C_WHITE}" "${C_RESET}"
    printf "   %b(On your setup this typically changes from '[root@archiso /]#' back to 'root@archiso ~ #'.)%b\n" "${C_WHITE}" "${C_RESET}"

    # --- THE FIX: Add swapoff to manual instructions ---
    print_step "2" "swapoff -a && umount -R /mnt" "Deactivate swap and unmount all partitions cleanly to flush changes to disk."
    print_warning "Failure to unmount before poweroff may result in data loss or filesystem issues."

    print_step "3" "poweroff" "Shutdown the system."

    printf "\n%b%s%b\n" "${C_CYAN}" "----------------------------------------" "${C_RESET}"
    printf "%b NEXT STEPS:%b\n" "${C_WHITE}${C_BOLD}" "${C_RESET}"
    printf " 1. Wait for the system to fully power down.\n"
    printf " 2. %bREMOVE the USB installation media.%b\n" "${C_RED}${C_BOLD}" "${C_RESET}"
    printf " 3. Power on the machine to boot into your new Arch Hyprland system.\n"
    printf " 4. Enter your username and password to start Hyprland after booting.\n"
    printf "%b%s%b\n\n" "${C_CYAN}" "----------------------------------------" "${C_RESET}"
}

show_auto_mode() {
    printf "Autonomous finish mode has been armed.\n"
    printf "This script does %bnot%b perform the final live-ISO-side steps itself.\n" "${C_BOLD}" "${C_RESET}"
    printf "Instead, it wrote a flag file and returned control to the caller.\n"

    printf "\n%bFlag file inside chroot:%b %s\n" "${C_WHITE}${C_BOLD}" "${C_RESET}" "${AUTO_FLAG_FILE}"
    printf "%bSame file from the live ISO:%b %s\n" "${C_WHITE}${C_BOLD}" "${C_RESET}" "/mnt${AUTO_FLAG_FILE}"

    printf "\n%bExpected next actions by the live-environment caller:%b\n" "${C_WHITE}${C_BOLD}" "${C_RESET}"
    printf " 1. Let the chrooted command finish and return to the live ISO.\n"
    # --- THE FIX: Add swapoff to autonomous instructions ---
    printf " 2. Run: %bswapoff -a && umount -R /mnt%b\n" "${C_YELLOW}${C_BOLD}" "${C_RESET}"
    printf " 3. Run: %bpoweroff%b\n" "${C_YELLOW}${C_BOLD}" "${C_RESET}"

    print_warning "If you launched this from an interactive 'arch-chroot /mnt' shell, you must still type 'exit' once yourself. A child script cannot exit its parent shell."
    printf "   %bOn your setup, if you still see '[root@archiso /]#', you are still inside the chroot.%b\n" "${C_WHITE}" "${C_RESET}"
}

main() {
    parse_args "$@"
    setup_terminal
    clear_screen
    print_banner

    printf "\nThe automated portion of the installation is finished.\n"

    prompt_for_auto_mode
    update_auto_flag

    printf "\n"
    if (( AUTO_MODE )); then
        show_auto_mode
    else
        show_manual_mode
    fi
}

main "$@"
