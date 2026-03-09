#!/usr/bin/env bash
# -----------------------------------------------------------------------------
# Elite DevOps GPU Driver Installer (v2026.07-Golden)
# -----------------------------------------------------------------------------
# Role:       System Architect
# Objective:  Hardware Detection & Driver Installation ONLY.
#             (No config modification, no mkinitcpio touches).
# Context:    Arch Linux (Rolling) / Wayland / Hyprland.
# Logic:      Sysfs Topology -> Strict lspci Fallback -> Ordered Classification.
# -----------------------------------------------------------------------------

# --- 1. STRICT MODE ---
set -euo pipefail
shopt -s extglob nullglob

# --- 2. GLOBAL STATE ---
HAS_INTEL=0
HAS_AMD=0
HAS_NVIDIA=0
NVIDIA_PCI_ADDRS=()
INTEL_PCI_ADDRS=()
FINAL_NVIDIA_DRIVER=""
FINAL_NVIDIA_UTILS=""

# --- 3. LOGGING UTILITIES ---
readonly BOLD=$'\033[1m'
readonly BLUE=$'\033[34m'
readonly GREEN=$'\033[32m'
readonly YELLOW=$'\033[33m'
readonly RED=$'\033[31m'
readonly RESET=$'\033[0m'

log_info() { printf "%s[INFO]%s %s\n" "${BLUE}${BOLD}" "${RESET}" "$*"; }
log_ok()   { printf "%s[OK]%s %s\n" "${GREEN}${BOLD}" "${RESET}" "$*"; }
log_warn() { printf "%s[WARN]%s %s\n" "${YELLOW}" "${RESET}" "$*" >&2; }
log_err()  { printf "%s[ERROR]%s %s\n" "${RED}${BOLD}" "${RESET}" "$*" >&2; }
die()      { log_err "$1"; exit "${2:-1}"; }

# --- 4. ARGUMENT PARSING ---
AUTO_MODE=0
for arg in "$@"; do
    if [[ "$arg" == "--auto" ]]; then
        AUTO_MODE=1
    fi
done

# --- 5. PRE-FLIGHT CHECKS ---
if [[ $EUID -eq 0 ]]; then
    die "Do not run as root. Run as normal user; sudo will be invoked automatically."
fi

check_deps() {
    local missing=()
    if ! command -v lspci &>/dev/null; then missing+=("pciutils"); fi
    if ! command -v pacman &>/dev/null; then missing+=("pacman"); fi
    if ! command -v grep &>/dev/null; then missing+=("grep"); fi
    if ! command -v sudo &>/dev/null; then missing+=("sudo"); fi
    
    if [[ ${#missing[@]} -gt 0 ]]; then
        die "Missing dependencies: ${missing[*]}. Please install them."
    fi
}

# --- 6. TOPOLOGY DETECTION ---
detect_topology() {
    log_info "Scanning GPU Topology..."
    
    # --- PHASE 1: Sysfs (Preferred - Active GPUs) ---
    for card_path in /sys/class/drm/card+([0-9]); do
        local vendor_file="$card_path/device/vendor"
        [[ ! -r "$vendor_file" ]] && continue
        
        local vendor_id
        vendor_id=$(cat "$vendor_file")
        vendor_id=${vendor_id,,} 
        
        # Get PCI Address
        local pci_link pci_addr
        pci_link=$(readlink -f "$card_path/device")
        pci_addr="${pci_link##*/}"

        case "$vendor_id" in
            "0x8086") 
                HAS_INTEL=1 
                INTEL_PCI_ADDRS+=("$pci_addr")
                ;;
            "0x1002") HAS_AMD=1 ;;
            "0x10de") 
                HAS_NVIDIA=1
                NVIDIA_PCI_ADDRS+=("$pci_addr")
                ;;
        esac
    done

    # --- PHASE 2: lspci Fallback (Granular & Strict) ---
    if [[ "$HAS_INTEL" -eq 0 ]]; then
        # Check for Intel VGA (0300) or Display (0380) using command grouping
        local intel_line
        intel_line=$({ lspci -n -d 8086::0300; lspci -n -d 8086::0380; } 2>/dev/null | head -n1)
        
        if [[ -n "$intel_line" ]]; then
            HAS_INTEL=1
            local addr="${intel_line%% *}"
            INTEL_PCI_ADDRS+=("$addr")
            log_info "Detected INTEL GPU via lspci fallback ($addr)."
        fi
    fi

    if [[ "$HAS_AMD" -eq 0 ]]; then
        if [[ -n $({ lspci -d 1002::0300; lspci -d 1002::0380; } 2>/dev/null | head -n1) ]]; then
            HAS_AMD=1
            log_info "Detected AMD GPU via lspci fallback."
        fi
    fi

    if [[ "$HAS_NVIDIA" -eq 0 ]]; then
        # Strictly filter by Class 0300 (VGA) and 0302 (3D Controller)
        while IFS= read -r line; do
            HAS_NVIDIA=1
            local pci_addr="${line%% *}"
            NVIDIA_PCI_ADDRS+=("$pci_addr")
            log_info "Detected NVIDIA GPU ($pci_addr) via lspci fallback."
        done < <({ lspci -n -d 10de::0300; lspci -n -d 10de::0302; } 2>/dev/null)
    fi

    # --- PHASE 3: Virtualization Guard (The Fix) ---
    if [[ $((HAS_INTEL + HAS_AMD + HAS_NVIDIA)) -eq 0 ]]; then
        # Check for VirtIO (1af4), VMware (15ad), or VirtualBox (80ee) Graphics
        local vm_gpu
        vm_gpu=$({ lspci -d 1af4::0300; lspci -d 15ad::0300; lspci -d 80ee::0300; } 2>/dev/null | head -n1)

        if [[ -n "$vm_gpu" ]]; then
            log_warn "Virtual GPU detected ($vm_gpu)."
            log_ok "Skipping driver installation for Virtual Machine."
            exit 0  # <--- Graceful Exit (Success)
        fi

        # If strict check fails AND it's not a known VM GPU:
        die "No GPUs detected via Sysfs OR lspci. Hardware failure or deep sleep?"
    fi
}

# --- 7. INTEL CLASSIFICATION ENGINE ---
# Distinguishes between i965 (Legacy), iHD+MediaSDK (Broadwell-IceLake), and iHD+VPL (TigerLake/Arc)
INTEL_DRIVER_MODE="" # "legacy", "modern_msdk", "modern_vpl"

classify_intel() {
    [[ "$HAS_INTEL" -eq 0 ]] && return

    log_info "Classifying Intel Hardware..."
    
    local pci_addr="${INTEL_PCI_ADDRS[0]}"
    local intel_name
    intel_name=$(lspci -s "$pci_addr" -mm -v 2>/dev/null | grep -i "^Device:" | cut -f2-) || true
    if [[ -z "$intel_name" ]]; then intel_name=$(lspci -s "$pci_addr" 2>/dev/null); fi
    
    log_info "  Found: $intel_name"

    # --- REGEX HEURISTICS ---
    local gen12_regex='Arc|Iris Xe|UHD Graphics 7[0-9]{2}|TigerLake|AlderLake|RaptorLake|MeteorLake|ArrowLake|LunarLake'
    local gen8_11_regex='HD Graphics 5[0-9]{2}|HD Graphics 6[0-9]{2}|UHD Graphics 6[0-9]{2}|Iris Plus|Iris Pro 6|Broadwell|Skylake|Kaby|Coffee|Ice'
    local legacy_regex='HD Graphics 4[0-9]{2}|HD Graphics 3000|HD Graphics 2[0-9]{2}|Haswell|Ivy Bridge|Sandy Bridge|GMA'

    if echo "$intel_name" | grep -qEi "($gen12_regex)"; then
        INTEL_DRIVER_MODE="modern_vpl"
        log_ok "  -> Gen 12+ (Xe/Arc) detected. Using iHD + OneVPL."
    elif echo "$intel_name" | grep -qEi "($gen8_11_regex)"; then
        INTEL_DRIVER_MODE="modern_msdk"
        log_ok "  -> Gen 8-11 detected. Using iHD + Media SDK."
    elif echo "$intel_name" | grep -qEi "($legacy_regex)"; then
        INTEL_DRIVER_MODE="legacy"
        log_warn "  -> Pre-Broadwell detected. Using legacy i965 driver."
    else
        INTEL_DRIVER_MODE="modern_vpl"
        log_warn "  -> Unknown Intel Generation. Defaulting to Modern (iHD+VPL)."
    fi
}

# --- 8. NVIDIA CLASSIFICATION ENGINE (ORDERED PRECEDENCE) ---
classify_nvidia() {
    [[ "$HAS_NVIDIA" -eq 0 ]] && return

    local detected_arch="open"
    
    log_info "Classifying NVIDIA Hardware..."

    for pci_addr in "${NVIDIA_PCI_ADDRS[@]}"; do
        local gpu_info
        gpu_info=$(lspci -s "$pci_addr" -mm -v 2>/dev/null | grep -i "^Device:" | cut -f2-) || true
        if [[ -z "$gpu_info" ]]; then gpu_info=$(lspci -s "$pci_addr" 2>/dev/null); fi
        
        log_info "  Found: $gpu_info ($pci_addr)"

        # --- PRECEDENCE LOGIC (Fixes TITAN Overlaps) ---
        # Checks are performed in order. The first match determines the architecture.

        # 1. Maxwell Exception: GTX 750 / 750 Ti / 745 are Maxwell (Legacy/Proprietary)
        local maxwell_exception='GTX 750|GTX 745'
        
        # 2. Turing+ (Modern/Open): Includes TITAN RTX
        local turing_regex='GTX 16[0-9]{2}|RTX [2-9][0-9]{3}|TITAN RTX|RTX PRO|Quadro RTX|RTX A[0-9]+|\b[ALHTB][1-9][0-9]{0,3}\b|MX[45][0-9]0'

        # 3. Legacy (Proprietary Blob): Includes TITAN X, Xp, V.
        #    This MUST run before Kepler regex to catch "TITAN X" before "TITAN" matches.
        local legacy_regex='GTX 9[0-9]{2}|GTX 10[0-9]{2}|GT 10[0-9]{2}|Quadro [MPG][V0-9]+|TITAN [XpV]|Tesla [MPV][0-9]+|MX[1-3][0-9]0'
        
        # 4. Kepler/Fermi (Ancient/Unsupported): Includes TITAN (original), Z, Black.
        #    Safe to use wide regex here because Maxwell/Legacy cases were already caught above.
        local kepler_regex='GTX? 6[0-9]{2}|GTX? 7[0-9]{2}|TITAN|Quadro K[0-9]+|Tesla K[0-9]+'

        if echo "$gpu_info" | grep -qEi "($maxwell_exception)"; then
            log_warn "  -> Maxwell (Gen 1) detected. Downgrading to Proprietary/Closed driver."
            detected_arch="legacy"
        elif echo "$gpu_info" | grep -qEi "($turing_regex)"; then
            log_ok "  -> Turing+ detected."
        elif echo "$gpu_info" | grep -qEi "($legacy_regex)"; then
            log_warn "  -> Maxwell/Pascal/Volta detected. Downgrading to Proprietary/Closed driver."
            detected_arch="legacy"
        elif echo "$gpu_info" | grep -qEi "($kepler_regex)"; then
            log_err "  -> Kepler/Fermi GPU detected ($gpu_info)."
            log_err "  -> This architecture requires 'nvidia-470xx-dkms' (AUR)."
            detected_arch="ancient"
            break
        else
            log_warn "  -> Unknown Architecture. Assuming Modern (Turing+)."
        fi
    done

    if [[ "$detected_arch" == "ancient" ]]; then
        die "Unsupported Legacy GPU detected. Please install drivers manually via AUR."
    elif [[ "$detected_arch" == "legacy" ]]; then
        FINAL_NVIDIA_DRIVER="nvidia-dkms" 
        FINAL_NVIDIA_UTILS="nvidia-utils"
        log_warn "Selection: nvidia-dkms (Proprietary/Closed)."
    else
        FINAL_NVIDIA_DRIVER="nvidia-open-dkms"
        FINAL_NVIDIA_UTILS="nvidia-utils"
        log_ok "Selection: nvidia-open-dkms (Modern Open Kernel Modules)."
    fi
}

# --- 9. INSTALLATION ENGINE ---
get_kernel_headers() {
    local headers=()
    log_info "Scanning installed kernels..." >&2 
    
    # Regex scan for standard headers, explicitly excluding API headers
    while read -r pkg; do
        headers+=("$pkg")
    done < <(pacman -Qq | grep -E '^linux.*-headers$' | grep -v 'api')

    if [[ ${#headers[@]} -eq 0 ]]; then
        log_warn "No kernel header packages found. DKMS will fail!" >&2
    else
        log_ok "Targeting headers: ${headers[*]}" >&2
    fi
    echo "${headers[*]}"
}

get_multilib_state() {
    if grep -qE '^\s*\[multilib\]' /etc/pacman.conf; then echo "1"; else echo "0"; fi
}

install_routine() {
    local vendor="$1"
    shift
    local pkg_list=("$@")
    
    if [[ ${#pkg_list[@]} -eq 0 ]]; then return; fi

    echo ""
    log_info "Preparing ${vendor} Drivers:"
    for p in "${pkg_list[@]}"; do echo "   - $p"; done
    
    if [[ "$AUTO_MODE" -eq 0 ]]; then
        read -rp "  Proceed with installation? [y/N] " choice || true
        case "${choice:-N}" in
            [yY]*) ;;
            *) log_warn "Skipping $vendor."; return ;;
        esac
    fi

    local cmd=(sudo pacman -S --needed --noconfirm)
    "${cmd[@]}" "${pkg_list[@]}"
    log_ok "$vendor drivers installed."
}

# --- 10. MAIN ---
main() {
    check_deps
    detect_topology
    classify_intel
    classify_nvidia

    local use_multilib
    use_multilib=$(get_multilib_state)
    local kernel_headers=()
    read -r -a kernel_headers <<< "$(get_kernel_headers)"

    # --- INTEL INSTALLATION ---
    if [[ "$HAS_INTEL" -eq 1 ]]; then
        local intel_pkgs=("mesa" "vulkan-intel" "intel-gpu-tools" "vulkan-icd-loader" "vulkan-tools" "mesa-utils" "libva-utils")
        
        if [[ "$INTEL_DRIVER_MODE" == "modern_vpl" ]]; then
            intel_pkgs+=("intel-media-driver" "vpl-gpu-rt")
        elif [[ "$INTEL_DRIVER_MODE" == "modern_msdk" ]]; then
            intel_pkgs+=("intel-media-driver" "intel-media-sdk")
        else
            intel_pkgs+=("libva-intel-driver")
        fi

        if [[ "$use_multilib" -eq 1 ]]; then
            intel_pkgs+=("lib32-mesa" "lib32-vulkan-intel")
        fi
        
        install_routine "INTEL" "${intel_pkgs[@]}"
    fi

    # --- AMD INSTALLATION ---
    if [[ "$HAS_AMD" -eq 1 ]]; then
        local amd_pkgs=("mesa" "vulkan-radeon" "libva-mesa-driver" "vulkan-icd-loader" "vulkan-tools" "mesa-utils" "libva-utils")
        [[ "$use_multilib" -eq 1 ]] && amd_pkgs+=("lib32-mesa" "lib32-vulkan-radeon")
        install_routine "AMD" "${amd_pkgs[@]}"
    fi

    # --- NVIDIA INSTALLATION ---
    if [[ "$HAS_NVIDIA" -eq 1 ]]; then
        local nvidia_pkgs=("${FINAL_NVIDIA_DRIVER}" "${FINAL_NVIDIA_UTILS}" "egl-wayland" "libva-nvidia-driver" "opencl-nvidia" "vulkan-icd-loader" "vulkan-tools" "mesa-utils")
        
        if [[ ${#kernel_headers[@]} -gt 0 ]]; then
             nvidia_pkgs+=("${kernel_headers[@]}")
        fi

        if [[ "$use_multilib" -eq 1 ]]; then
            nvidia_pkgs+=("lib32-nvidia-utils" "lib32-opencl-nvidia")
        fi
        
        install_routine "NVIDIA" "${nvidia_pkgs[@]}"
    fi

    echo ""
    log_info "--- Installation Complete ---"
    if [[ "$HAS_NVIDIA" -eq 1 ]]; then
        echo "  [NVIDIA SPECIFIC]"
        echo "  1. Edit /etc/mkinitcpio.conf (Add 'nvidia nvidia_modeset nvidia_uvm nvidia_drm' to MODULES)"
        echo "  2. Run 'sudo mkinitcpio -P'"
        echo "  3. Add 'nvidia_drm.modeset=1' to your kernel boot parameters."
    fi
    echo "  [ALL USERS]"
    echo "  1. Reboot your system."
}

main "$@"
