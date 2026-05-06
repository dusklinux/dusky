#!/usr/bin/env bash
# -----------------------------------------------------------------------------
# MODULE: PACSTRAP (HARDWARE-VERIFIED & OFFLINE CACHE EDITION)
# -----------------------------------------------------------------------------
set -euo pipefail

# --- Colors ---
if [[ -t 1 ]]; then
    readonly C_BOLD=$'\033[1m'
    readonly C_GREEN=$'\033[32m'
    readonly C_YELLOW=$'\033[33m'
    readonly C_RED=$'\033[31m'
    readonly C_RESET=$'\033[0m'
else
    readonly C_BOLD="" C_GREEN="" C_YELLOW="" C_RED="" C_RESET=""
fi

# --- Configuration ---
readonly MOUNT_POINT="/mnt"
AUTO_MODE=0
USE_GENERIC_FIRMWARE=0
HW_CACHE=""
VM_DETECTED=0

# Base packages every system needs. Added zsh for immediate payload delivery.
FINAL_PACKAGES=(
    base base-devel linux linux-headers mkinitcpio
    neovim btrfs-progs dosfstools git zsh
    networkmanager yazi linux-firmware-other
)

# --- Logging Helpers ---
log_info() { echo -e "${C_GREEN}[INFO]${C_RESET} $*"; }
log_warn() { echo -e "${C_YELLOW}[WARN]${C_RESET} $*"; }
log_err()  { echo -e "${C_RED}[ERROR]${C_RESET} $*"; }

usage() {
    cat <<EOF
Usage: ${0##*/} [--auto|-a] [--help|-h]

Options:
  -a, --auto   Run autonomously (no prompts)
  -h, --help   Show this help
EOF
}

# --- Helper: Wait for pacman lock ---
wait_for_pacman_lock() {
    while [[ -f /var/lib/pacman/db.lck ]]; do
        log_warn "Waiting for pacman lock..."
        sleep 3
    done
}

# --- Helper: Yes/No Prompt ---
prompt_yes_no() {
    local prompt="$1"
    local default="${2:-N}"
    local reply

    case "$default" in
        Y|y)
            read -r -p "$prompt [Y/n] " reply || return 1
            [[ "${reply,,}" =~ ^(y|yes|)$ ]]
            ;;
        *)
            read -r -p "$prompt [y/N] " reply || return 1
            [[ "${reply,,}" =~ ^(y|yes)$ ]]
            ;;
    esac
}

# --- Helper: Check if package exists in Arch repos ---
package_exists() {
    wait_for_pacman_lock
    pacman -Si "$1" &>/dev/null
}

# --- Helper: Ensure a live ISO tool exists ---
ensure_live_tool() {
    local cmd="$1"
    local pkg="$2"

    if ! command -v "$cmd" &>/dev/null; then
        wait_for_pacman_lock
        pacman -S --noconfirm --needed "$pkg" &>/dev/null
    fi
}

# --- Helper: Unified Hardware Cache (PCI + USB) ---
get_hw_cache() {
    if [[ -z "$HW_CACHE" ]]; then
        local pci_data=""
        local usb_data=""

        ensure_live_tool lspci pciutils
        ensure_live_tool lsusb usbutils

        pci_data=$(lspci -mm 2>/dev/null) || pci_data=""
        usb_data=$(lsusb 2>/dev/null) || usb_data=""

        HW_CACHE=$(printf '%s\n%s' "$pci_data" "$usb_data")

        # Virtualization Guard (VirtIO, VMware, VirtualBox)
        if printf '%s\n' "$HW_CACHE" | grep -iEq "1af4|15ad|80ee|VirtualBox|VMware|VirtIO"; then
            VM_DETECTED=1
        fi
    fi
    printf '%s\n' "$HW_CACHE"
}

# --- Helper: Detect Hardware & Add Package ---
detect_and_add() {
    local name="$1"
    local pattern="$2"
    local pkg="$3"

    echo -ne "   > Scanning for ${name}... "

    if (( VM_DETECTED )); then
        echo -e "${C_YELLOW}SKIPPED (VM Environment)${C_RESET}"
        return 0
    fi

    if get_hw_cache | grep -iEq "$pattern"; then
        echo -e "${C_GREEN}FOUND${C_RESET}"

        if (( USE_GENERIC_FIRMWARE )); then
            echo -e "     -> ${C_YELLOW}Generic mode active; bypassing specific package request.${C_RESET}"
            return 0
        fi

        if package_exists "$pkg"; then
            echo -e "     -> Queuing Verified Package: ${C_BOLD}${pkg}${C_RESET}"
            FINAL_PACKAGES+=("$pkg")
        else
            echo -e "     -> ${C_YELLOW}Hardware found, but package '$pkg' missing in repo.${C_RESET}"
            echo -e "     -> Switching to Safe Mode (Generic Firmware)."
            USE_GENERIC_FIRMWARE=1
        fi
    else
        echo "NO"
    fi
}

# --- Helper: Remove duplicate packages while preserving order ---
dedupe_final_packages() {
    local pkg
    local -A seen=()
    local -a deduped=()

    for pkg in "${FINAL_PACKAGES[@]}"; do
        if [[ -z ${seen[$pkg]+x} ]]; then
            deduped+=("$pkg")
            seen["$pkg"]=1
        fi
    done

    FINAL_PACKAGES=("${deduped[@]}")
}

# --- Helper: Normalize target temp dir permissions ---
normalize_target_tmp_permissions() {
    mkdir -p "$MOUNT_POINT/var/tmp"
    chmod 1777 "$MOUNT_POINT/var/tmp"
}

# --- Parse Arguments ---
for arg in "$@"; do
    case "$arg" in
        -a|--auto)
            AUTO_MODE=1
            ;;
        -h|--help)
            usage
            exit 0
            ;;
        *)
            log_err "Unknown option: $arg"
            usage
            exit 1
            ;;
    esac
done

# ==============================================================================
# 1. SAFETY PRE-FLIGHT CHECKS
# ==============================================================================
echo -e "${C_BOLD}=== PACSTRAP: HARDWARE-VERIFIED EDITION ===${C_RESET}"

if (( EUID != 0 )); then
    log_err "This script must be run as root."
    exit 1
fi

if ! mountpoint -q "$MOUNT_POINT"; then
    log_err "$MOUNT_POINT is not a mountpoint. Mount your partitions first."
    exit 1
fi

if (( ! AUTO_MODE )); then
    if [[ -t 0 ]]; then
        if prompt_yes_no "Enable autonomous mode for this run (skip all prompts)?" "N"; then
            AUTO_MODE=1
            log_info "Autonomous mode enabled."
        fi
    else
        AUTO_MODE=1
        log_warn "Non-interactive session detected. Enabling autonomous mode."
    fi
fi

# ==============================================================================
# 2. CPU MICROCODE
# ==============================================================================
CPU_VENDOR=$(awk '/^vendor_id/ {print $3; exit}' /proc/cpuinfo)

case "$CPU_VENDOR" in
    GenuineIntel)
        log_info "CPU: Intel Detected"
        FINAL_PACKAGES+=("intel-ucode")
        ;;
    AuthenticAMD)
        log_info "CPU: AMD Detected"
        FINAL_PACKAGES+=("amd-ucode")
        ;;
    *)
        log_warn "Unknown CPU Vendor ($CPU_VENDOR). Proceeding without specific ucode."
        ;;
esac

# ==============================================================================
# 3. PERIPHERAL DETECTION (PCI & USB)
# ==============================================================================
log_info "Scanning Hardware Topography (PCI + USB)..."

get_hw_cache >/dev/null

if (( VM_DETECTED )); then
    log_warn "Virtual Machine detected. Bypassing bare-metal firmware discovery."
fi

# -- GRAPHICS --
detect_and_add "Nvidia GPU"         "10de|nvidia"            "linux-firmware-nvidia"
detect_and_add "AMD GPU (Modern)"   "1002|amdgpu|navi|rdna"  "linux-firmware-amdgpu"
detect_and_add "AMD GPU (Legacy)"   "\b(radeon|ati)\b"       "linux-firmware-radeon"

# -- NETWORKING & BLUETOOTH --
detect_and_add "Intel Network/BT"   "intel.*(network|wireless|bluetooth)|8086" "linux-firmware-intel"
detect_and_add "Mediatek WiFi/BT"   "mediatek"               "linux-firmware-mediatek"
detect_and_add "Broadcom WiFi/BT"   "broadcom"               "linux-firmware-broadcom"
detect_and_add "Atheros WiFi/BT"    "atheros"                "linux-firmware-atheros"
detect_and_add "Realtek Eth/WiFi"   "realtek|\brtl"          "linux-firmware-realtek"

# -- AUDIO --
detect_and_add "Intel SOF Audio"    "audio.*intel|8086"      "sof-firmware"
detect_and_add "Cirrus Logic Audio" "cirrus"                 "linux-firmware-cirrus"

# ==============================================================================
# 4. FINAL PACKAGE ASSEMBLY
# ==============================================================================
if (( USE_GENERIC_FIRMWARE )); then
    log_warn "Fallback Triggered: Consolidating to generic linux-firmware."

    CLEAN_LIST=()
    for pkg in "${FINAL_PACKAGES[@]}"; do
        [[ "$pkg" == linux-firmware-* || "$pkg" == "sof-firmware" ]] || CLEAN_LIST+=("$pkg")
    done
    FINAL_PACKAGES=("${CLEAN_LIST[@]}" "linux-firmware")
else
    if ! (( VM_DETECTED )); then
        FINAL_PACKAGES+=("linux-firmware-whence")
    fi
fi

dedupe_final_packages

# ==============================================================================
# 5. EXECUTION
# ==============================================================================
echo ""
echo -e "${C_BOLD}Final Package List:${C_RESET}"
printf '%s\n' "${FINAL_PACKAGES[@]}"
echo ""

if (( AUTO_MODE )); then
    log_info "Autonomous mode active. Proceeding with pacstrap."
else
    if ! prompt_yes_no "Ready to run pacstrap?" "Y"; then
        log_warn "Aborted by user."
        exit 0
    fi
fi

log_info "Normalizing target temporary directory permissions..."
normalize_target_tmp_permissions

echo "Installing..."
wait_for_pacman_lock

if (( AUTO_MODE )); then
    # Feed default answers to pacstrap/pacman prompts without tripping pipefail.
    pacstrap -K "$MOUNT_POINT" "${FINAL_PACKAGES[@]}" --needed < <(yes "")
else
    pacstrap -K "$MOUNT_POINT" "${FINAL_PACKAGES[@]}" --needed
fi

echo -e "\n${C_GREEN}Pacstrap Complete.${C_RESET}"
