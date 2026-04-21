#!/usr/bin/env bash
#
# Arch Linux Configuration Script (Chroot Phase)
# Optimized for Bash 5+ | Arch Linux
#

# Auto mode example:
# TARGET_USER='myuser' ROOT_PASS='testroot' USER_PASS='testuser' ./003_post_chroot.sh --auto

# --- 1. Safety & Environment ---
set -Eeuo pipefail
IFS=$'\n\t'

# --- 2. Visuals & Helpers ---
readonly BOLD=$'\e[1m'
readonly RESET=$'\e[0m'
readonly GREEN=$'\e[32m'
readonly BLUE=$'\e[34m'
readonly RED=$'\e[31m'
readonly YELLOW=$'\e[33m'

log_info()    { printf "${BLUE}[INFO]${RESET} %s\n" "$1"; }
log_success() { printf "${GREEN}[SUCCESS]${RESET} %s\n" "$1"; }
log_error()   { printf "${RED}[ERROR]${RESET} %s\n" "$1"; }
log_step()    { printf "\n${BOLD}${YELLOW}>>> STEP: %s${RESET}\n" "$1"; }

on_error() {
    local exit_code=$?
    log_error "Command failed (exit ${exit_code}) at line $1: $2"
    exit "$exit_code"
}

trap 'on_error "$LINENO" "$BASH_COMMAND"' ERR
trap 'printf "${RESET}\n"' EXIT

# --- 3. Defaults & CLI ---
DEFAULT_HOSTNAME="${DEFAULT_HOSTNAME:-workstation}"
DEFAULT_TZ="${DEFAULT_TZ:-Asia/Kolkata}"
AUTO_MODE="${AUTO_MODE:-0}"
readonly USER_GROUPS='wheel,input,audio,video,storage,optical,network,lp,power,games,rfkill'

usage() {
    cat <<EOF
Usage: ${0##*/} [--auto|-a] [--help|-h]

Modes:
  Interactive:
    Prompts for missing hostname/user values.
    Passwords are set interactively with passwd and retried until accepted.

  Auto (--auto or AUTO_MODE=1):
    Uses default for hostname.
    Requires TARGET_USER, ROOT_PASS, and USER_PASS. If omitted, it will
    gracefully fallback to interactive prompts (requires an active TTY).

Optional environment variables:
  TARGET_HOSTNAME   Default: ${DEFAULT_HOSTNAME}
  TARGET_USER       Required (will prompt if not provided)
  TARGET_TZ         Default: detected timezone or ${DEFAULT_TZ}
  ROOT_PASS         Password for root
  USER_PASS         Password for user
EOF
}

while (($# > 0)); do
    case "$1" in
        -a|--auto)
            AUTO_MODE=1
            ;;
        -h|--help)
            usage
            exit 0
            ;;
        *)
            log_error "Unknown option: $1"
            usage
            exit 1
            ;;
    esac
    shift
done

has_tty() {
    [[ -t 0 ]]
}

ensure_tty() {
    if ! has_tty; then
        log_error "Interactive input required, but no TTY is available."
        printf "Provide required values via environment, or rerun with ${BOLD}--auto${RESET}.\n"
        exit 1
    fi
}

prompt_yes_no() {
    local reply=""
    read -r -p "$1 [y/N]: " reply || return 1
    [[ "$reply" =~ ^[Yy]([Ee][Ss])?$ ]]
}

var_nonempty() {
    local var_name="$1"
    [[ -n "${!var_name-}" ]]
}

can_run_strict_auto() {
    var_nonempty ROOT_PASS && var_nonempty USER_PASS
}

apply_password_from_var() {
    local account="$1"
    local pass_var="$2"
    printf '%s:%s\n' "$account" "${!pass_var}" | chpasswd
}

set_password_interactive() {
    local account="$1"
    local label="$2"

    ensure_tty

    if ! command -v passwd &>/dev/null; then
        log_error "passwd command not found."
        exit 1
    fi

    while true; do
        log_info "Set password for ${label}:"
        if passwd "$account"; then
            return 0
        fi

        log_error "Password update for ${label} failed. The entries may not have matched, or the password may have been rejected."
        log_info "Please try again. Press Ctrl-C to abort."
    done
}

# --- 4. Pre-flight Check ---
log_step "Environment Validation"

if command -v findmnt &>/dev/null; then
    FSTYPE="$(findmnt -no FSTYPE / 2>/dev/null || true)"
    if [[ "$FSTYPE" =~ ^(overlay|airootfs)$ ]]; then
        log_error "Execution halted: Detected Live ISO root ($FSTYPE)."
        printf "Please run: ${BOLD}arch-chroot /mnt${RESET} first.\n"
        exit 1
    fi
fi

ROOT_STAT="$(stat -c '%d:%i' / 2>/dev/null || true)"
INIT_ROOT_STAT="$(stat -c '%d:%i' /proc/1/root/. 2>/dev/null || true)"

if [[ -z "$ROOT_STAT" || -z "$INIT_ROOT_STAT" ]]; then
    log_error "Execution halted: Unable to verify chroot state."
    exit 1
fi

if [[ "$ROOT_STAT" == "$INIT_ROOT_STAT" ]]; then
    log_error "Execution halted: Not running inside a chroot."
    printf "Please run: ${BOLD}arch-chroot /mnt${RESET} first.\n"
    exit 1
fi

log_success "Chroot environment confirmed."

# --- 5. Resilient Timezone Resolution ---
get_dynamic_timezone() {
    local tz=""
    local fallback_tz="$DEFAULT_TZ"

    if command -v curl &>/dev/null; then
        tz="$(curl -sSfL --retry 3 --retry-delay 1 --connect-timeout 3 https://ipapi.co/timezone 2>/dev/null || true)"
        if [[ -z "$tz" ]]; then
            tz="$(curl -sSfL --retry 2 --connect-timeout 3 http://ip-api.com/line?fields=timezone 2>/dev/null || true)"
        fi
    fi

    if [[ -n "$tz" && -f "/usr/share/zoneinfo/$tz" ]]; then
        printf '%s\n' "$tz"
    else
        printf '%s\n' "$fallback_tz"
    fi
}

# --- 6. Smart Mode Negotiation ---
if [[ "$AUTO_MODE" != "1" ]]; then
    if has_tty; then
        if can_run_strict_auto; then
            if prompt_yes_no "Run in autonomous mode (no further prompts; uses defaults for missing hostname/timezone)"; then
                AUTO_MODE=1
            fi
        else
            log_info "Strict autonomous mode not offered: ROOT_PASS and USER_PASS are not preseeded."
        fi
    else
        if can_run_strict_auto; then
            AUTO_MODE=1
            log_info "No TTY detected. Switching to autonomous mode."
        else
            log_error "No TTY detected and strict autonomous mode requirements are not met."
            printf "Set ${BOLD}ROOT_PASS${RESET} and ${BOLD}USER_PASS${RESET}, then rerun with ${BOLD}--auto${RESET} or in the same headless environment.\n"
            exit 1
        fi
    fi
fi

# --- 7. Data Ingestion ---
log_step "Configuration Ingestion"

TARGET_TZ="${TARGET_TZ:-$(get_dynamic_timezone)}"

if [[ ! -f "/usr/share/zoneinfo/$TARGET_TZ" ]]; then
    log_error "Invalid timezone: $TARGET_TZ"
    exit 1
fi

# Hostname Setup
if [[ -n "${TARGET_HOSTNAME:-}" ]]; then
    FINAL_HOST="$TARGET_HOSTNAME"
elif [[ "$AUTO_MODE" == "1" ]]; then
    FINAL_HOST="$DEFAULT_HOSTNAME"
else
    ensure_tty
    if ! read -r -p "Enter hostname [Default: ${DEFAULT_HOSTNAME}]: " INPUT_HOST; then
        log_error "Failed to read hostname."
        exit 1
    fi
    FINAL_HOST="${INPUT_HOST:-$DEFAULT_HOSTNAME}"
fi

# Username Setup
if [[ -n "${TARGET_USER:-}" ]]; then
    FINAL_USER="$TARGET_USER"
else
    if [[ "$AUTO_MODE" == "1" ]] && ! has_tty; then
        log_error "Auto mode requires TARGET_USER to be set when no TTY is present."
        exit 1
    fi
    ensure_tty
    while true; do
        read -r -p "Enter username for the new system: " INPUT_USER
        if [[ -n "${INPUT_USER:-}" ]]; then
            FINAL_USER="$INPUT_USER"
            break
        else
            log_error "Username cannot be empty. Please enter a valid username."
        fi
    done
fi

if [[ -z "${FINAL_HOST:-}" || -z "${FINAL_USER:-}" ]]; then
    log_error "Hostname and username cannot be empty. Aborting deployment."
    exit 1
fi

if [[ "$AUTO_MODE" == "1" ]]; then
    if ! can_run_strict_auto; then
        if ! has_tty; then
            log_error "Auto mode requires non-empty ROOT_PASS and USER_PASS when no TTY is present."
            exit 1
        else
            log_info "AUTO_MODE enabled but credentials missing. Will prompt interactively."
        fi
    fi
fi

export -n ROOT_PASS USER_PASS 2>/dev/null || true
readonly TARGET_TZ FINAL_HOST FINAL_USER

if [[ "$AUTO_MODE" == "1" ]]; then
    log_success "Parameters secured. Proceeding in autonomous mode..."
else
    log_success "Parameters secured. Proceeding with interactive deployment..."
fi

# --- 8. Main Execution ---

# === System Time ===
log_step "Configuring Timezone: $TARGET_TZ"
ln -sf "/usr/share/zoneinfo/$TARGET_TZ" /etc/localtime
hwclock --systohc
log_success "Timezone linked and hardware clock synced."

# === System Language ===
log_step "Configuring Locales"
sed -i 's/^#\?\s*\(en_US.UTF-8\s\+UTF-8\)/\1/' /etc/locale.gen
locale-gen
printf "LANG=en_US.UTF-8\n" > /etc/locale.conf
log_success "System language generated and configured."

# === Hostname ===
log_step "Setting Hostname"
printf "%s\n" "$FINAL_HOST" > /etc/hostname
log_success "Hostname set to: $FINAL_HOST"

# === Root Password ===
log_step "Setting Root Password"
if var_nonempty ROOT_PASS; then
    apply_password_from_var root ROOT_PASS
elif [[ "$AUTO_MODE" == "1" ]]; then
    if var_nonempty USER_PASS; then
        log_info "Auto mode: Using predefined USER_PASS for root."
        apply_password_from_var root USER_PASS
    else
        ensure_tty
        log_info "Auto mode: Setting unified password for root and ${FINAL_USER}."
        while true; do
            read -r -s -p "Enter unified password for root and ${FINAL_USER}: " SHARED_PASS
            echo
            read -r -s -p "Retype unified password: " SHARED_PASS_CONFIRM
            echo
            if [[ -z "$SHARED_PASS" ]]; then
                log_error "Password cannot be empty. Please try again."
            elif [[ "$SHARED_PASS" != "$SHARED_PASS_CONFIRM" ]]; then
                log_error "Passwords do not match. Please try again."
            else
                ROOT_PASS="$SHARED_PASS"
                USER_PASS="$SHARED_PASS"
                apply_password_from_var root ROOT_PASS
                break
            fi
        done
    fi
else
    set_password_interactive root "root"
fi
log_success "Root credentials secured."

# === User Account ===
log_step "Provisioning User: $FINAL_USER"
pacman -S --needed --noconfirm zsh

if id -- "$FINAL_USER" &>/dev/null; then
    log_info "User '$FINAL_USER' exists. Verifying state..."
    usermod -a -G "$USER_GROUPS" -- "$FINAL_USER"

    CURRENT_SHELL="$(getent passwd "$FINAL_USER" | cut -d: -f7)"
    if [[ "$CURRENT_SHELL" != "/usr/bin/zsh" ]]; then
        log_info "Enforcing ZSH as default shell..."
        usermod -s /usr/bin/zsh -- "$FINAL_USER"
    fi
else
    useradd -m -G "$USER_GROUPS" -s /usr/bin/zsh -- "$FINAL_USER"
fi

if var_nonempty USER_PASS; then
    apply_password_from_var "$FINAL_USER" USER_PASS
else
    set_password_interactive "$FINAL_USER" "user '$FINAL_USER'"
fi

unset ROOT_PASS USER_PASS
log_success "User account provisioned and secured."

# === Wheel Group Rights ===
log_step "Configuring Sudoers"

if ! command -v visudo &>/dev/null; then
    log_error "visudo not found. Install sudo before running this script."
    exit 1
fi

mkdir -p /etc/sudoers.d
printf '%%wheel ALL=(ALL:ALL) ALL\n' | EDITOR='tee' visudo -f /etc/sudoers.d/10_wheel >/dev/null
chmod 0440 /etc/sudoers.d/10_wheel
visudo -cf /etc/sudoers >/dev/null
log_success "Wheel group privileges granted."

printf "\n${GREEN}${BOLD}Post-Chroot configuration complete. Proceeding to next orchestrator step...${RESET}\n"
