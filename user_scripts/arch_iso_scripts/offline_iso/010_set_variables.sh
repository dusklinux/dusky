#!/usr/bin/env bash
# ==============================================================================
#  ARCH ORCHESTRATOR - INLINE CREDENTIAL INGESTION (010)
#  Context: Collects credentials and stages them for Phase 2 chroot extraction.
# ==============================================================================
set -Eeuo pipefail
IFS=$'\n\t'

# --- 1. Environment Checks ---
if (( EUID != 0 )); then
    printf "\e[31m[ERROR]\e[0m This script must be run as root.\n" >&2
    exit 1
fi

if [[ ! -t 0 ]]; then
    printf "\e[31m[ERROR]\e[0m Interactive TTY required to securely collect credentials.\n" >&2
    exit 1
fi

# --- 2. Visuals & Traps ---
readonly BOLD=$'\e[1m'
readonly RESET=$'\e[0m'
readonly CYAN=$'\e[36m'
readonly GREEN=$'\e[32m'
readonly RED=$'\e[31m'
readonly YELLOW=$'\e[33m'

trap 'printf "${RESET}\n"; exit 130' INT

printf "${CYAN}${BOLD}"
printf "===============================================================\n"
printf "                    Dusky Automated Installer                  \n"
printf "===============================================================\n"
printf "${RESET}\n"
printf "Welcome. Please provide your system credentials upfront.\n"
printf "These credentials will be securely staged for Phase 2 deployment.\n\n"

# --- 3. Credential Ingestion ---
declare INGESTED_USER=""
declare INGESTED_PASS=""
declare INGESTED_PASS_VERIFY=""

while true; do
    printf "%s👤 Enter desired username: %s" "$BOLD" "$RESET"
    read -r INGESTED_USER || { printf "\n%sInput aborted. Exiting.%s\n" "$RED" "$RESET" >&2; exit 1; }

    if [[ -z "$INGESTED_USER" ]]; then
        printf "%sUsername cannot be empty. Please try again.%s\n" "$RED" "$RESET"
    elif [[ "$INGESTED_USER" == "root" ]]; then
        printf "%sCannot use 'root' as the target user. Please pick another name.%s\n" "$RED" "$RESET"
    elif [[ ! "$INGESTED_USER" =~ ^[a-z_][a-z0-9_-]*$ ]]; then
        printf "%sInvalid username. Must start with a lowercase letter or underscore, and contain only lowercase letters, numbers, hyphens, or underscores.%s\n" "$RED" "$RESET"
    elif (( ${#INGESTED_USER} > 32 )); then
        printf "%sUsername is too long (maximum 32 characters).%s\n" "$RED" "$RESET"
    else
        break
    fi
done

while true; do
    printf "%s🔑 Enter password (will be hidden): %s" "$BOLD" "$RESET"
    read -s -r INGESTED_PASS || { printf "\n%sInput aborted. Exiting.%s\n" "$RED" "$RESET" >&2; exit 1; }
    printf "\n"

    if [[ -z "$INGESTED_PASS" ]]; then
        printf "%sPassword cannot be empty. Please try again.%s\n\n" "$RED" "$RESET"
        continue
    fi

    printf "%s🔁 Verify password: %s" "$BOLD" "$RESET"
    read -s -r INGESTED_PASS_VERIFY || { printf "\n%sInput aborted. Exiting.%s\n" "$RED" "$RESET" >&2; exit 1; }
    printf "\n"

    if [[ "$INGESTED_PASS" != "$INGESTED_PASS_VERIFY" ]]; then
        printf "%sPasswords do not match. Please try again.%s\n\n" "$RED" "$RESET"
        unset INGESTED_PASS INGESTED_PASS_VERIFY
    else
        printf "%sPassword verified successfully!%s\n\n" "$GREEN" "$RESET"
        unset INGESTED_PASS_VERIFY
        break
    fi
done

# --- 4. Secure State Persistence ---
printf "%sStaging credentials for boundary crossing...%s\n" "$YELLOW" "$RESET"

readonly CREDS_FILE="$(pwd)/.arch_credentials"

# Use 'install -m 600' to atomically create the file with restrictive permissions
# from birth. This eliminates the TOCTOU race that exists with 'touch + chmod',
# where the file would briefly be world-readable under a typical umask of 0022.
install -m 600 /dev/null "$CREDS_FILE"

# We use printf %q to ensure passwords with special characters (spaces, quotes, etc.)
# are safely escaped to prevent bash injection vulnerabilities downstream.
if ! cat <<EOF > "$CREDS_FILE"
export TARGET_USER=$(printf '%q' "$INGESTED_USER")
export USER_PASS=$(printf '%q' "$INGESTED_PASS")
export ROOT_PASS=$(printf '%q' "$INGESTED_PASS")
export AUTO_MODE=1
EOF
then
    printf "%s[ERROR] Failed to write credentials file. Aborting.%s\n" "$RED" "$RESET" >&2
    rm -f "$CREDS_FILE"
    exit 1
fi

# Clear sensitive variables from process memory now that they have been persisted.
unset INGESTED_USER INGESTED_PASS

printf "%s%sCredentials secured. Yielding back to orchestrator...%s\n" "$GREEN" "$BOLD" "$RESET"
exit 0
