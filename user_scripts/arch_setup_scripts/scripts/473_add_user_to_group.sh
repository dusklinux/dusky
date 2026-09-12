#!/usr/bin/env bash
#d: Add or remove a user from groups

set -euo pipefail

# ==========================================
# ===        USER CONFIGURATION          ===
# ==========================================
# Easily add or remove groups from this array.
# Separate each group with a space.

TARGET_GROUPS=(wheel input audio video render storage optical network lp power games rfkill)

AUTO_MODE=0

# --- Forensic Argument Parsing ---
while [[ $# -gt 0 ]]; do
    case "$1" in
        -a|--auto)
            AUTO_MODE=1
            shift
            ;;
        -h|--help)
            printf "Usage: %s [--auto | -a]\n" "$0"
            printf "Idempotent script to add the current user to Arch standard groups.\n"
            exit 0
            ;;
        *)
            printf "Critical Error: Unknown argument '%s'\n" "$1" >&2
            printf "Usage: %s [--auto | -a]\n" "$0" >&2
            exit 1
            ;;
    esac
done

# --- Resilient User Identification ---
# Fallback chain: SUDO -> DOAS -> Direct Execution
if [[ -n "${SUDO_USER:-}" ]]; then
    REAL_USER="$SUDO_USER"
elif [[ -n "${DOAS_USER:-}" ]]; then
    REAL_USER="$DOAS_USER"
else
    REAL_USER="${USER:-$(whoami)}"
fi

# --- Privilege Escalation & Context Switching ---
if [[ $EUID -ne 0 ]]; then
    printf "Root privileges are required to modify user groups.\n"
    printf "Elevating privileges for user: %s...\n" "$REAL_USER"
    
    ELEVATE_ARGS=()
    if [[ $AUTO_MODE -eq 1 ]]; then
        ELEVATE_ARGS+=("--auto")
    fi

    if command -v sudo >/dev/null 2>&1; then
        exec sudo bash "$0" "${ELEVATE_ARGS[@]}"
    elif command -v doas >/dev/null 2>&1; then
        exec doas bash "$0" "${ELEVATE_ARGS[@]}"
    else
        printf "Critical Error: Neither 'sudo' nor 'doas' was found. Please run as root.\n" >&2
        exit 1
    fi
fi

# --- Post-Elevation Guardrails ---
if [[ "$REAL_USER" == "root" ]]; then
    printf "Critical Error: Cannot safely determine your desktop user.\n" >&2
    printf "You ran this from a pure root shell (e.g., 'su -'). \n" >&2
    printf "Please run this script as your regular user (it will auto-elevate).\n" >&2
    exit 1
fi

if ! id "$REAL_USER" >/dev/null 2>&1; then
    printf "Critical Error: Resolved user '%s' does not exist in the system.\n" "$REAL_USER" >&2
    exit 1
fi

# --- Display Current State ---
# Capture groups as a raw string (e.g., "wheel audio video") and formatted with padding spaces
CURRENT_GROUPS_RAW=$(id -nG "$REAL_USER")
CURRENT_GROUPS_PADDED=" $CURRENT_GROUPS_RAW "

printf "\n============================================\n"
printf "User: %s\n" "$REAL_USER"
printf "Current Memberships: %s\n" "$CURRENT_GROUPS_RAW"
printf "============================================\n\n"

# --- Idempotent State Evaluation ---
MISSING_GROUPS=()

for group in "${TARGET_GROUPS[@]}"; do
    # 1. Verify the group exists in the system's database
    if ! getent group "$group" >/dev/null 2>&1; then
        printf "Warning: Group '%s' does not exist in this Arch installation. Skipping.\n" "$group"
        continue
    fi

    # 2. Pure bash exact-match check (zero external forks)
    if [[ "$CURRENT_GROUPS_PADDED" != *" $group "* ]]; then
        MISSING_GROUPS+=("$group")
    fi
done

# --- Execution Phase ---
if [[ ${#MISSING_GROUPS[@]} -eq 0 ]]; then
    printf "✔ System state is already perfect.\n"
    printf "User '%s' is already a member of all specified target groups.\n" "$REAL_USER"
    exit 0
fi

# Apply system changes based on mode
if [[ $AUTO_MODE -eq 1 ]]; then
    printf "Autonomous mode enabled. Applying missing groups automatically...\n"
    for group in "${MISSING_GROUPS[@]}"; do
        usermod -aG "$group" "$REAL_USER"
        printf "  [+] Appended to %s\n" "$group"
    done
else
    printf "Manual mode enabled. You will be prompted for each missing group.\n"
    for group in "${MISSING_GROUPS[@]}"; do
        read -r -p "Add user '$REAL_USER' to group '$group'? [y/N] " response
        case "${response,,}" in
            y|yes)
                usermod -aG "$group" "$REAL_USER"
                printf "  [+] Added to %s\n" "$group"
                ;;
            *)
                printf "  [-] Skipped %s\n" "$group"
                ;;
        esac
    done
fi

printf "\n✔ Script execution completed for user '%s'.\n" "$REAL_USER"
printf "⚠️ IMPORTANT: Linux requires a new session for group changes to take effect.\n"
printf "   Please log out of Hyprland and log back in, or reboot your system.\n"
