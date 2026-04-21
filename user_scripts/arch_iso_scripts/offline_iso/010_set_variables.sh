#!/usr/bin/env bash
# ==============================================================================
#  ARCH ORCHESTRATOR - INLINE CREDENTIAL INGESTION (010)
#  Context: Collects credentials and stages them for Phase 2 chroot extraction.
# ==============================================================================
set -Eeuo pipefail
IFS=$'\n\t'

# ── 1. Pre-Flight Checks (raw output — gum not yet verified) ──────────────────
if (( EUID != 0 )); then
    printf "\e[31m[ERROR]\e[0m This script must be run as root.\n" >&2
    exit 1
fi

if [[ ! -t 0 ]]; then
    printf "\e[31m[ERROR]\e[0m Interactive TTY required to securely collect credentials.\n" >&2
    exit 1
fi

if ! command -v gum &>/dev/null; then
    printf "\e[33m[INFO]\e[0m  'gum' not found. Installing via pacman...\n"
    if ! pacman -S --noconfirm gum; then
        printf "\e[31m[ERROR]\e[0m Failed to install 'gum'. Please install it manually:\n" >&2
        printf "               \e[1mpacman -S gum\e[0m\n" >&2
        exit 1
    fi
    printf "\e[32m[OK]\e[0m    'gum' installed successfully.\n\n"
fi

# ── 2. Trap & Colour Palette (Catppuccin Mocha) ───────────────────────────────
readonly RESET=$'\e[0m'
trap 'printf "${RESET}\n"; exit 130' INT

readonly C_CYAN="#89DCEB"
readonly C_GREEN="#A6E3A1"
readonly C_RED="#F38BA8"
readonly C_YELLOW="#F9E2AF"
readonly C_TEXT="#CDD6F4"
readonly C_SUBTEXT="#BAC2DE"
readonly C_OVERLAY="#6C7086"

# ── 3. Header ─────────────────────────────────────────────────────────────────
printf "\n"
gum style \
    --border double \
    --align center \
    --border-foreground "$C_CYAN" \
    --foreground "$C_TEXT" \
    --bold \
    --padding "1 6" \
    --margin "0 2" \
    "✦   Dusky Automated Installer   ✦"

printf "\n"
gum style \
    --foreground "$C_SUBTEXT" \
    --margin "0 4" \
    "Welcome. Please provide your system credentials upfront."
gum style \
    --foreground "$C_OVERLAY" \
    --margin "0 4" \
    "The same password is used for LUKS2 encryption, root, and user, you can change it later"
printf "\n"

# ── 4. Credential Ingestion ───────────────────────────────────────────────────
declare INGESTED_USER=""
declare INGESTED_PASS=""
declare INGESTED_PASS_VERIFY=""

# ── 4a. Username ──────────────────────────────────────────────────────────────
gum style \
    --foreground "$C_CYAN" \
    --bold \
    --margin "0 4" \
    "User Account"

while true; do
    INGESTED_USER=$(
        gum input \
            --prompt "  👤  " \
            --prompt.foreground "$C_CYAN" \
            --placeholder "enter desired username" \
            --placeholder.foreground "$C_OVERLAY" \
            --cursor.foreground "$C_CYAN" \
            --width 40
    ) || {
        printf "\n"
        gum style --foreground "$C_RED" --margin "0 4" "✗  Input aborted. Exiting."
        printf "\n"
        exit 1
    }

    if [[ -z "$INGESTED_USER" ]]; then
        gum style --foreground "$C_RED" --margin "0 4" \
            "✗  Username cannot be empty. Please try again."
    elif [[ "$INGESTED_USER" == "root" ]]; then
        gum style --foreground "$C_RED" --margin "0 4" \
            "✗  Cannot use 'root' as the target user. Please pick another name."
    elif [[ ! "$INGESTED_USER" =~ ^[a-z_][a-z0-9_-]*$ ]]; then
        gum style --foreground "$C_RED" --margin "0 4" \
            "✗  Invalid username. Must start with a lowercase letter or underscore,"$'\n'"   and contain only lowercase letters, numbers, hyphens, or underscores."
    elif (( ${#INGESTED_USER} > 32 )); then
        gum style --foreground "$C_RED" --margin "0 4" \
            "✗  Username is too long (maximum 32 characters)."
    else
        gum style --foreground "$C_GREEN" --margin "0 4" \
            "✓  Username accepted: $INGESTED_USER"
        break
    fi
done

printf "\n"

# ── 4b. Password ──────────────────────────────────────────────────────────────
gum style \
    --foreground "$C_CYAN" \
    --bold \
    --margin "0 4" \
    "Set Password"

while true; do
    INGESTED_PASS=$(
        gum input \
            --password \
            --prompt "  🔑  " \
            --prompt.foreground "$C_CYAN" \
            --placeholder "enter password" \
            --placeholder.foreground "$C_OVERLAY" \
            --cursor.foreground "$C_CYAN" \
            --width 40
    ) || {
        printf "\n"
        gum style --foreground "$C_RED" --margin "0 4" "✗  Input aborted. Exiting."
        printf "\n"
        exit 1
    }

    if [[ -z "$INGESTED_PASS" ]]; then
        gum style --foreground "$C_RED" --margin "0 4" \
            "✗  Password cannot be empty. Please try again."
        printf "\n"
        continue
    fi

    INGESTED_PASS_VERIFY=$(
        gum input \
            --password \
            --prompt "  🔁  " \
            --prompt.foreground "$C_CYAN" \
            --placeholder "verify password" \
            --placeholder.foreground "$C_OVERLAY" \
            --cursor.foreground "$C_CYAN" \
            --width 40
    ) || {
        printf "\n"
        gum style --foreground "$C_RED" --margin "0 4" "✗  Input aborted. Exiting."
        printf "\n"
        exit 1
    }

    if [[ "$INGESTED_PASS" != "$INGESTED_PASS_VERIFY" ]]; then
        gum style --foreground "$C_RED" --margin "0 4" \
            "✗  Passwords do not match. Please try again."
        printf "\n"
        unset INGESTED_PASS INGESTED_PASS_VERIFY
    else
        gum style --foreground "$C_GREEN" --margin "0 4" \
            "✓  Password verified successfully!"
        unset INGESTED_PASS_VERIFY
        break
    fi
done

printf "\n"

# ── 5. Secure State Persistence ───────────────────────────────────────────────
gum spin \
    --spinner dot \
    --spinner.foreground "$C_YELLOW" \
    --title " Staging credentials for Phase 2..." \
    --title.foreground "$C_YELLOW" \
    -- sleep 0.8

readonly CREDS_FILE="$(pwd)/.arch_credentials"

# Use 'install -m 600' to atomically create the file with restrictive permissions
# from birth. This eliminates the TOCTOU race that exists with 'touch + chmod',
# where the file would briefly be world-readable under a typical umask of 0022.
install -m 600 /dev/null "$CREDS_FILE"

# We use printf %q to ensure passwords with special characters (spaces, quotes,
# etc.) are safely escaped to prevent bash injection vulnerabilities downstream.
if ! cat <<EOF > "$CREDS_FILE"
export TARGET_USER=$(printf '%q' "$INGESTED_USER")
export USER_PASS=$(printf '%q' "$INGESTED_PASS")
export ROOT_PASS=$(printf '%q' "$INGESTED_PASS")
export AUTO_MODE=1
EOF
then
    gum style \
        --foreground "$C_RED" \
        --bold \
        --margin "0 4" \
        "✗  [ERROR] Failed to write credentials file. Aborting." >&2
    rm -f "$CREDS_FILE"
    exit 1
fi

# Clear sensitive variables from process memory now that they have been persisted.
unset INGESTED_USER INGESTED_PASS

printf "\n"
gum style \
    --border normal \
    --border-foreground "$C_GREEN" \
    --foreground "$C_GREEN" \
    --bold \
    --padding "0 3" \
    --margin "0 2" \
    "✦  Credentials secured. Yielding back to orchestrator...  ✦"
printf "\n"

exit 0
