#!/usr/bin/env bash
# -----------------------------------------------------------------------------
# MODULE: LIVE ENVIRONMENT PREP (OFFLINE SAFE)
# Description: Font, Cowspace, Battery, Time, Keyring
# -----------------------------------------------------------------------------
set -euo pipefail

readonly C_BOLD=$'\033[1m'
readonly C_GREEN=$'\033[32m'
readonly C_YELLOW=$'\033[33m'
readonly C_BLUE=$'\033[34m'
readonly C_RED=$'\033[31m'
readonly C_RESET=$'\033[0m'

msg_info() { printf '%b[INFO]%b %s\n' "$C_BLUE" "$C_RESET" "$1"; }
msg_ok()   { printf '%b[OK]%b   %s\n' "$C_GREEN" "$C_RESET" "$1"; }
msg_warn() { printf '%b[WARN]%b %s\n' "$C_YELLOW" "$C_RESET" "$1"; }
msg_err()  { printf '%b[ERR ]%b %s\n' "$C_RED" "$C_RESET" "$1" >&2; }
die()      { msg_err "$1"; exit 1; }

usage() {
    cat <<'EOF'
Usage: 002_environment_prep.sh [--auto|-a] [--cowspace SIZE]

Options:
  -a, --auto          Run autonomously with no interactive prompts.
  --cowspace SIZE     Resize Arch ISO cowspace to SIZE (example: 500M, 1G).
  -h, --help          Show this help.

Environment variables:
  AUTO_MODE=1         Same as --auto
  COWSPACE_SIZE=1G    Same as --cowspace 1G
EOF
}

is_yes() {
    case "${1:-}" in
        [Yy]|[Yy][Ee][Ss]) return 0 ;;
        *) return 1 ;;
    esac
}

valid_cowspace() {
    [[ "${1:-}" =~ ^[0-9]+[GgMm]$ ]]
}

AUTO_MODE="${AUTO_MODE:-0}"
case "$AUTO_MODE" in
    1|true|TRUE|yes|YES|y|Y) AUTO_MODE=1 ;;
    *) AUTO_MODE=0 ;;
esac

COWSPACE_SIZE="${COWSPACE_SIZE:-}"

while (($#)); do
    case "$1" in
        -a|--auto|auto)
            AUTO_MODE=1
            ;;
        --cowspace)
            (($# >= 2)) || die "--cowspace requires a value"
            COWSPACE_SIZE="${2// /}"
            shift
            ;;
        --cowspace=*)
            COWSPACE_SIZE="${1#*=}"
            COWSPACE_SIZE="${COWSPACE_SIZE// /}"
            ;;
        -h|--help)
            usage
            exit 0
            ;;
        *)
            die "Unknown option: $1"
            ;;
    esac
    shift
done

(( EUID == 0 )) || die "This script must be run as root."

printf '%b=== PRE-INSTALL ENVIRONMENT SETUP ===%b\n' "$C_BOLD" "$C_RESET"

if (( AUTO_MODE == 0 )) && [[ ! -t 0 ]]; then
    msg_info "No interactive terminal detected; enabling autonomous mode."
    AUTO_MODE=1
fi

if (( AUTO_MODE == 0 )); then
    AUTO_REPLY=""
    if ! read -r -p ":: Run in autonomous mode (no further prompts)? [y/N]: " AUTO_REPLY; then
        AUTO_REPLY=""
    fi
    if is_yes "$AUTO_REPLY"; then
        AUTO_MODE=1
        msg_info "Autonomous mode enabled."
    fi
else
    msg_info "Autonomous mode enabled."
fi

# 1. Console Font
msg_info "Setting console font..."
setfont latarcyrheb-sun32 || msg_warn "Could not set font. Continuing..."

# 2. Battery Threshold
BAT_DIR=""
if [[ -d /sys/class/power_supply ]]; then
    BAT_DIR=$(find /sys/class/power_supply -maxdepth 1 -name "BAT*" -print -quit)
fi

if [[ -n "$BAT_DIR" ]]; then
    BAT_CTRL="$BAT_DIR/charge_control_end_threshold"
    if [[ -f "$BAT_CTRL" ]]; then
        if [[ -w "$BAT_CTRL" ]]; then
            CURRENT_THRESHOLD=""
            if [[ -r "$BAT_CTRL" ]]; then
                CURRENT_THRESHOLD=$(<"$BAT_CTRL")
            fi

            if [[ "$CURRENT_THRESHOLD" == "60" ]]; then
                msg_info "Battery limit already set to 60%."
            else
                echo "60" > "$BAT_CTRL"
                msg_ok "Battery limit set to 60%."
            fi
        else
            msg_warn "Battery threshold control exists but is not writable. Skipping."
        fi
    else
        msg_info "Battery detected, but charge threshold control is unsupported. Skipping."
    fi
else
    msg_info "No battery detected. Skipping battery threshold."
fi

# 3. Cowspace
COWSPACE_PATH="/run/archiso/cowspace"
if mountpoint -q "$COWSPACE_PATH" 2>/dev/null; then
    TOTAL_RAM=$(free -h | awk '/^Mem:/ {print $2}')
    CURRENT_COW=$(df -h "$COWSPACE_PATH" | awk 'NR==2 {print $2}')

    msg_info "System RAM: $TOTAL_RAM | Current Cowspace: $CURRENT_COW"

    USER_COW="${COWSPACE_SIZE// /}"

    if [[ -n "$USER_COW" ]]; then
        if ! valid_cowspace "$USER_COW"; then
            msg_warn "Invalid Cowspace value '$USER_COW'. Skipping resize."
            USER_COW=""
        fi
    elif (( AUTO_MODE )); then
        msg_info "Autonomous mode: keeping current Cowspace ($CURRENT_COW)."
    else
        if ! read -r -p ":: Enter new Cowspace size (e.g. 1G) [Leave empty to keep default]: " USER_COW; then
            USER_COW=""
        fi
        USER_COW="${USER_COW// /}"

        if [[ -z "$USER_COW" ]]; then
            msg_info "No input detected. Keeping current Cowspace ($CURRENT_COW)."
        elif ! valid_cowspace "$USER_COW"; then
            msg_warn "Invalid format '$USER_COW'. Skipping resize."
            USER_COW=""
        fi
    fi

    if [[ -n "$USER_COW" ]]; then
        msg_info "Resizing Cowspace to $USER_COW..."
        if mount -o remount,size="$USER_COW" "$COWSPACE_PATH"; then
            NEW_SIZE=$(df -h "$COWSPACE_PATH" | awk 'NR==2 {print $2}')
            msg_ok "Cowspace successfully resized: $NEW_SIZE"
        else
            msg_warn "Remount failed. Keeping previous size."
        fi
    fi
else
    msg_info "Cowspace mount not detected. Skipping Cowspace handling."
fi

# 4. Time
msg_info "Configuring Time (NTP)..."
timedatectl set-ntp true

# 5. Pacman Init (Offline Safe)
msg_info "Initializing Local Pacman Keys..."

msg_info "1/2: pacman-key --init"
pacman-key --init
sleep 1

msg_info "2/2: pacman-key --populate archlinux"
pacman-key --populate archlinux
sleep 1

msg_ok "Environment Ready."
