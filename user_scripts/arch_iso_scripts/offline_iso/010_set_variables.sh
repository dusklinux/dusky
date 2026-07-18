#!/usr/bin/env bash
# ==============================================================================
#  ARCH ORCHESTRATOR - INLINE CREDENTIAL INGESTION (010)
#  DUSKY_INTERACTIVE=true
#  Context: Collects credentials and stages them for Phase 2 chroot extraction.
# ==============================================================================
set -Eeuo pipefail
IFS=$'\n\t'

# ── 1. Pre-Flight Checks ──────────────────────────────────────────────────────
if (( EUID != 0 )); then
    printf "\e[1;31m[ERROR]\e[0m This script must be run as root.\n" >&2
    exit 1
fi

if [[ ! -t 0 ]]; then
    printf "\e[1;31m[ERROR]\e[0m Interactive TTY required to securely collect credentials.\n" >&2
    exit 1
fi

# ── 2. Term & Basic ANSI Colors (TTY Safe) ────────────────────────────────────
readonly RESET='\e[0m'
readonly C_CYAN='\e[1;36m'
readonly C_GREEN='\e[1;32m'
readonly C_RED='\e[1;31m'
readonly C_YELLOW='\e[1;33m'
readonly C_WHITE='\e[1;37m'
readonly C_BOLD='\e[1m'

trap 'printf "${RESET}\n"; exit 130' INT

clear_screen() {
    # Clears the screen and the scrollback buffer for a true "page" refresh
    printf '\e[H\e[2J\e[3J'
}

print_header() {
    printf "\n${C_CYAN}================================================================\n"
    printf "                  DUSKY AUTOMATED INSTALLER\n"
    printf "================================================================${RESET}\n\n"
}

# ── 3. Argument Parsing ───────────────────────────────────────────────────────
declare PRESET_ENCRYPT=""

while [[ "$#" -gt 0 ]]; do
    case "$1" in
        --encrypt)
            PRESET_ENCRYPT=1
            shift
            ;;
        --no-encrypt|--no_encrypt)
            PRESET_ENCRYPT=0
            shift
            ;;
        *)
            printf "\n  ${C_RED}[ERROR] Unknown parameter passed: %s${RESET}\n" "$1" >&2
            exit 1
            ;;
    esac
done

# ── 4. Credential Ingestion (Wizard UI) ───────────────────────────────────────
declare INGESTED_USER=""
declare INGESTED_PASS=""
declare INGESTED_PASS_VERIFY=""
declare INGESTED_ENCRYPT=""
declare INGESTED_ENCRYPT_INPUT="" # Declared explicitly for safe unsetting
declare ENCRYPT_TEXT=""

# Master loop to allow restarting the process if the user rejects the final review
while true; do

    # ── 4a. Username Page ─────────────────────────────────────────────────────────
    while true; do
        clear_screen
        print_header
        
        printf "  ${C_WHITE}Welcome. Please provide your system credentials upfront.${RESET}\n\n"
        
        printf "${C_CYAN}"
        cat << 'EOF'
          _   _ ___ ___ ___ _  _   _   __  __ ___ 
         | | | / __| __| _ \ \| | /_\ |  \/  | __|
         | |_| \__ \ _||   / .` |/ _ \| |\/| | _| 
          \___/|___/___|_|_\_|\_/_/ \_\_|  |_|___|
EOF
        printf "${RESET}\n\n"
        
        printf "    ==> Enter desired username: "
        read -r INGESTED_USER || { printf "\n\n  ${C_RED}[!] Input aborted. Exiting.${RESET}\n"; exit 1; }

        if [[ -z "$INGESTED_USER" ]]; then
            printf "\n  ${C_RED}[!] Username cannot be empty.${RESET}\n"
            sleep 1.5
        elif [[ "$INGESTED_USER" == "root" ]]; then
            printf "\n  ${C_RED}[!] Cannot use 'root' as the target user.${RESET}\n"
            sleep 1.5
        elif [[ ! "$INGESTED_USER" =~ ^[a-z_][a-z0-9_-]*$ ]]; then
            printf "\n  ${C_RED}[!] Invalid username. Must start with a lowercase letter or underscore,\n"
            printf "      and contain only lowercase letters, numbers, hyphens, or underscores.${RESET}\n"
            sleep 3
        elif (( ${#INGESTED_USER} > 32 )); then
            printf "\n  ${C_RED}[!] Username is too long (maximum 32 characters).${RESET}\n"
            sleep 1.5
        else
            break
        fi
    done

    # ── 4b. Password Page ─────────────────────────────────────────────────────────
    while true; do
        clear_screen
        print_header
        
        printf "  ${C_GREEN}[✓] Account targeted for: ${C_BOLD}%s${RESET}\n" "$INGESTED_USER"
        printf "  ${C_WHITE}The same password is used for root and user (and encryption, if enabled).${RESET}\n\n"
        
        printf "${C_CYAN}"
        cat << 'EOF'
          ___  _   ___ _____      _____  ___ ___ 
         | _ \/_\ / __/ __\ \    / / _ \| _ \   \
         |  _/ _ \\__ \__ \\ \/\/ / (_) |   / |) |
         |_|/_/ \_\___/___/ \_/\_/ \___/|_|_\___/
EOF
        printf "${RESET}\n\n"
        
        printf "    ==> Enter password: "
        read -rs INGESTED_PASS || { printf "\n\n  ${C_RED}[!] Input aborted. Exiting.${RESET}\n"; exit 1; }
        echo

        if [[ -z "$INGESTED_PASS" ]]; then
            printf "\n  ${C_RED}[!] Password cannot be empty.${RESET}\n"
            sleep 1.5
            continue
        fi

        printf "    ==> Verify password: "
        read -rs INGESTED_PASS_VERIFY || { printf "\n\n  ${C_RED}[!] Input aborted. Exiting.${RESET}\n"; exit 1; }
        echo

        if [[ "$INGESTED_PASS" != "$INGESTED_PASS_VERIFY" ]]; then
            printf "\n  ${C_RED}[!] Passwords do not match. Please try again.${RESET}\n"
            unset INGESTED_PASS INGESTED_PASS_VERIFY
            sleep 1.5
        else
            break
        fi
    done
    
    # ── 4c. Encryption Page ───────────────────────────────────────────────────────
    
    # If a flag was passed, apply it immediately and skip the interactive prompt
    if [[ -n "$PRESET_ENCRYPT" ]]; then
        INGESTED_ENCRYPT="$PRESET_ENCRYPT"
        if (( INGESTED_ENCRYPT == 1 )); then
            ENCRYPT_TEXT="Yes (LUKS2) [Passed via flag]"
        else
            ENCRYPT_TEXT="No (Plain BTRFS) [Passed via flag]"
        fi
    else
        while true; do
            clear_screen
            print_header

            printf "  ${C_GREEN}[✓] Account targeted for: ${C_BOLD}%s${RESET}\n" "$INGESTED_USER"
            printf "  ${C_GREEN}[✓] Password verified.${RESET}\n\n"

            printf "${C_CYAN}"
            cat << 'EOF'
          ___ _  _  ___ _____   _____ _____ 
         | __| \| |/ __| _ \ \ / / _ \_   _|
         | _|| .` | (__|   /\ V /|  _/ | |  
         |___|_|\_|\___|_|_\ |_| |_|   |_|  
EOF
            printf "${RESET}\n\n"

            printf "  ${C_WHITE}Would you like to encrypt your root partition (LUKS2)?${RESET}\n"
            printf "  If yes, the password you just entered will be used to unlock the drive.\n\n"

            printf "    ==> Encrypt system? [y/N]: "
            read -r INGESTED_ENCRYPT_INPUT || { printf "\n\n  ${C_RED}[!] Input aborted. Exiting.${RESET}\n"; exit 1; }

            if [[ "${INGESTED_ENCRYPT_INPUT,,}" == "y" || "${INGESTED_ENCRYPT_INPUT,,}" == "yes" ]]; then
                INGESTED_ENCRYPT=1
                ENCRYPT_TEXT="Yes (LUKS2)"
                break
            elif [[ -z "$INGESTED_ENCRYPT_INPUT" || "${INGESTED_ENCRYPT_INPUT,,}" == "n" || "${INGESTED_ENCRYPT_INPUT,,}" == "no" ]]; then
                INGESTED_ENCRYPT=0
                ENCRYPT_TEXT="No (Plain BTRFS)"
                break
            else
                printf "\n  ${C_RED}[!] Invalid choice. Please enter y or N.${RESET}\n"
                sleep 1.5
            fi
        done
    fi

    # ── 4d. Review & Confirm Page ─────────────────────────────────────────────────
    clear_screen
    print_header
    
    printf "${C_CYAN}"
    cat << 'EOF'
          ___  _____   _____  ___      __
         | _ \| __\ \ / /_ _|| __|\/\/ /
         |   /| _| \ V / | | | _|\    / 
         |_|_\|___| \_/ |___||___|\/\/  
EOF
    printf "${RESET}\n\n"

    printf "  ${C_WHITE}Please review your configuration before staging:${RESET}\n\n"
    printf "      Username   :  ${C_BOLD}${C_GREEN}%s${RESET}\n" "$INGESTED_USER"
    printf "      Password   :  ${C_BOLD}${C_GREEN}********${RESET}\n"
    printf "      Encryption :  ${C_BOLD}${C_GREEN}%s${RESET}\n\n" "$ENCRYPT_TEXT"
    
    printf "    ==> Are these details correct? [Y/n]: "
    read -r CONFIRM_CHOICE || { printf "\n\n  ${C_RED}[!] Input aborted. Exiting.${RESET}\n"; exit 1; }

    # Default to "Yes" if they just hit Enter, or explicitly type 'y' / 'yes'
    if [[ -z "$CONFIRM_CHOICE" || "${CONFIRM_CHOICE,,}" == "y" || "${CONFIRM_CHOICE,,}" == "yes" ]]; then
        break # Everything is correct, exit the master loop and proceed
    else
        printf "\n  ${C_YELLOW}[*] No problem. Let's try that again...${RESET}\n"
        unset INGESTED_USER INGESTED_PASS INGESTED_PASS_VERIFY INGESTED_ENCRYPT ENCRYPT_TEXT INGESTED_ENCRYPT_INPUT CONFIRM_CHOICE
        sleep 1.5
        # Loop continues, taking them back to the Username page
    fi

done

# ── 5. Secure State Persistence ───────────────────────────────────────────────
clear_screen
print_header

printf "  ${C_GREEN}[✓] Account created for: ${C_BOLD}%s${RESET}\n" "$INGESTED_USER"
printf "  ${C_GREEN}[✓] Password verified successfully!${RESET}\n\n"

printf "  ${C_YELLOW}[*] Staging credentials for Phase 2...${RESET}\n"
sleep 0.8

readonly CREDS_FILE="$(pwd)/.arch_credentials"

# Use 'install -m 600' to atomically create the file with restrictive permissions
# from birth. This eliminates the TOCTOU race that exists with 'touch + chmod'.
install -m 600 /dev/null "$CREDS_FILE"

# We use printf %q to ensure passwords with special characters (spaces, quotes,
# etc.) are safely escaped to prevent bash injection vulnerabilities downstream.
if ! cat <<EOF > "$CREDS_FILE"
export TARGET_USER=$(printf '%q' "$INGESTED_USER")
export USER_PASS=$(printf '%q' "$INGESTED_PASS")
export ROOT_PASS=$(printf '%q' "$INGESTED_PASS")
export ENCRYPT_ROOT=$(printf '%q' "$INGESTED_ENCRYPT")
export AUTO_MODE=1
EOF
then
    printf "\n  ${C_RED}[ERROR] Failed to write credentials file. Aborting.${RESET}\n" >&2
    rm -f "$CREDS_FILE"
    exit 1
fi

# Clear sensitive variables from process memory now that they have been persisted.
unset INGESTED_USER INGESTED_PASS INGESTED_PASS_VERIFY INGESTED_ENCRYPT INGESTED_ENCRYPT_INPUT ENCRYPT_TEXT

printf "\n${C_GREEN}================================================================\n"
printf " [*] Credentials secured. Yielding back to orchestrator...\n"
printf "================================================================${RESET}\n\n"

# Pause briefly so the user can read the success prompt before the orchestrator takes over
sleep 1.5
exit 0
