#!/usr/bin/env bash
# INTEL VA-API / QSV STACK DEPLOYER (5th Gen to Current)
# Optimized for Bash 5.3+ / Arch Linux / Hyprland ecosystem
# -----------------------------------------------------------------------------
# Resolves the VA-API (iHD/i965) and QuickSync (MFX/OneVPL) package split
# to dynamically satisfy the UWSM environment prober.
# -----------------------------------------------------------------------------

# 1. Safety & Strict Mode
set -euo pipefail

# 2. Privileges Check
if (( EUID != 0 )); then
    printf "\e[0;31m[ERROR]\e[0m This script must be run as root.\n" >&2
    exit 1
fi

# 3. Colors
readonly GREEN=$'\e[0;32m'
readonly YELLOW=$'\e[0;33m'
readonly BLUE=$'\e[0;34m'
readonly RED=$'\e[0;31m'
readonly BOLD=$'\e[1m'
readonly RESET=$'\e[0m'

# Global flag for autonomous execution
AUTO_MODE=0

detect_and_install() {
    printf "%s>>> ANALYZING SYSTEM HARDWARE...%s\n" "${BLUE}" "${RESET}"

    # --- STAGE 1: HARDWARE VERIFICATION (The "Horse") ---
    # Raw PCI bus check to prevent F-series/chroot execution bloat.
    local intel_gpu_present=0
    local pci_dev vendor class

    shopt -s nullglob
    for pci_dev in /sys/bus/pci/devices/*; do
        if [[ -f "$pci_dev/vendor" && -f "$pci_dev/class" ]]; then
            read -r vendor < "$pci_dev/vendor"
            read -r class < "$pci_dev/class"

            # Vendor 0x8086 = Intel. Class 0x030000 = VGA, 0x038000 = Display
            if [[ "$vendor" == "0x8086" ]] && [[ "$class" == 0x0300* || "$class" == 0x0380* ]]; then
                intel_gpu_present=1
                break
            fi
        fi
    done
    shopt -u nullglob

    if (( ! intel_gpu_present )); then
        printf "%s[SKIP]%s No Intel iGPU detected on the PCI bus.\n" "${YELLOW}" "${RESET}"
        printf "%s[SKIP]%s Skipping installation to prevent bloat on F-series/headless configurations.\n" "${YELLOW}" "${RESET}"
        return 0
    fi

    # --- STAGE 2: MICROARCHITECTURE PARSING (The "Cart") ---
    local cpuinfo
    cpuinfo=$(< /proc/cpuinfo) || {
        printf "%s[ERROR]%s Failed to read /proc/cpuinfo.\n" "${RED}" "${RESET}"
        exit 1
    }

    local model_name="${cpuinfo#*model name*: }"
    model_name="${model_name%%$'\n'*}"

    printf "%s[INFO]%s Detected CPU: %s%s%s\n" "${BLUE}" "${RESET}" "${BOLD}" "${model_name}" "${RESET}"

    local gen="" sku

    if [[ $model_name =~ i[3579]-([0-9]{4,5})[A-Za-z0-9]* ]]; then
        sku="${BASH_REMATCH[1]}"
        if [[ $sku == 1[0-9]* ]]; then
            gen="${sku:0:2}"
        else
            gen="${sku:0:1}"
        fi
    elif [[ $model_name =~ Core\(TM\)[[:space:]](Ultra[[:space:]])?[3579][[:space:]][12][0-9]{2}[A-Za-z]* ]]; then
        gen="14"
    elif [[ $model_name =~ Core\(TM\)[[:space:]]i[3579]-N[0-9]{3}[A-Za-z0-9]* ]]; then
        gen="12"
    elif [[ $model_name =~ Processor[[:space:]]N[0-9]{2,3} ]]; then
        gen="12"
    elif [[ $model_name =~ ([mM][357]?|i[3579])[-[:space:]]?((1[0-9]|[5-9])Y[0-9]{2})[A-Za-z0-9]* ]]; then
        gen="${BASH_REMATCH[2]}"
    elif [[ $model_name =~ i[35]-L[0-9]{2}G[0-9][A-Za-z0-9]* ]]; then
        gen="10"
    fi

    # --- STAGE 3: DRIVER POLICY & DEPLOYMENT ---
    if [[ -n "$gen" ]]; then
        local -a target_pkgs=()
        local driver_tier=""

        if (( gen >= 12 )); then
            # 12th+ Gen (Alder Lake to Current)
            # vpl-gpu-rt pulls in intel-media-driver automatically, but we declare both for strict idempotency.
            target_pkgs=("intel-media-driver" "vpl-gpu-rt")
            driver_tier="12th+ Gen (iHD + OneVPL)"
        elif (( gen >= 5 && gen <= 11 )); then
            # 5th-11th Gen (Broadwell to Rocket Lake)
            # intel-media-sdk pulls in intel-media-driver automatically.
            target_pkgs=("intel-media-sdk")
            driver_tier="5th-11th Gen (iHD + MFX)"
        else
            printf "%s[SKIP]%s Intel %s Gen CPU detected. Hardware is outside the 5th+ Gen support matrix.\n" "${YELLOW}" "${RESET}" "${gen}"
            return 0
        fi

        printf "%s[MATCH]%s Intel %s graphics hardware present.\n" "${GREEN}" "${RESET}" "${driver_tier}"
        printf "%s[INFO]%s Target Packages: %s%s%s\n" "${BLUE}" "${RESET}" "${BOLD}" "${target_pkgs[*]}" "${RESET}"

        if (( ! AUTO_MODE )); then
            printf "%s[PROMPT]%s Install required hardware acceleration stack? [Y/n]: " "${YELLOW}" "${RESET}"
            local confirm
            if ! IFS= read -r confirm; then
                printf "\n%s[INFO]%s No input received. Installation aborted.\n" "${BLUE}" "${RESET}"
                return 0
            fi
            if [[ "${confirm,,}" =~ ^(n|no)$ ]]; then
                printf "%s[INFO]%s Installation aborted by user.\n" "${BLUE}" "${RESET}"
                return 0
            fi
        fi

        printf "%s[RUN]%s Deploying pacman targets...\n" "${YELLOW}" "${RESET}"
        pacman -S --needed --noconfirm "${target_pkgs[@]}"

        printf "%s[SUCCESS]%s Hardware acceleration stack installed.\n" "${GREEN}" "${RESET}"
        return 0

    elif [[ $model_name =~ Intel ]]; then
        printf "%s[WARN]%s Intel GPU detected, but cannot definitively parse microarchitecture generation.\n" "${YELLOW}" "${RESET}"
        printf "%s[SKIP]%s Skipping installation to prevent driver mismatch.\n" "${YELLOW}" "${RESET}"
        return 0
    else
        printf "%s[SKIP]%s Non-Intel CPU detected. Module ignored.\n" "${YELLOW}" "${RESET}"
        return 0
    fi
}

main() {
    for arg in "$@"; do
        if [[ "$arg" == "--auto" || "$arg" == "-a" ]]; then
            AUTO_MODE=1
            break
        fi
    done

    if (( AUTO_MODE )); then
        printf "%s[INFO]%s Autonomous deployment initialized. Suppressing user prompts.\n" "${BLUE}" "${RESET}"
    fi

    detect_and_install
}

main "$@"
