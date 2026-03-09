#!/usr/bin/env bash
# -----------------------------------------------------------------------------
# Arch / Hyprland / UWSM GPU Configurator (v2026.08-Golden)
# -----------------------------------------------------------------------------
# Role:       System Architect
# Objective:  Topology selection + Active Dependency Management + Safe AQ mapping.
# Standards:  Bash 5.3+, Sysfs Parsing, Atomic Writes, Idempotency.
# -----------------------------------------------------------------------------

set -euo pipefail
shopt -s extglob nullglob inherit_errexit

readonly UWSM_CONFIG_DIR="${XDG_CONFIG_HOME:-$HOME/.config}/uwsm"
readonly ENV_DIR="$UWSM_CONFIG_DIR/env.d"
readonly OUTPUT_FILE="$ENV_DIR/gpu"

# Driver Paths for Active VA-API Probing
readonly DRI_DIR="/usr/lib/dri"
readonly NVIDIA_VAAPI_DRIVER="$DRI_DIR/nvidia_drv_video.so"
readonly INTEL_IHD_DRIVER="$DRI_DIR/iHD_drv_video.so"
readonly INTEL_I965_DRIVER="$DRI_DIR/i965_drv_video.so"
readonly AMD_VAAPI_DRIVER="$DRI_DIR/radeonsi_drv_video.so"

if [[ -t 1 ]]; then
    readonly BOLD=$'\033[1m'
    readonly BLUE=$'\033[34m'
    readonly GREEN=$'\033[32m'
    readonly YELLOW=$'\033[33m'
    readonly RED=$'\033[31m'
    readonly RESET=$'\033[0m'
else
    readonly BOLD=''
    readonly BLUE=''
    readonly GREEN=''
    readonly YELLOW=''
    readonly RED=''
    readonly RESET=''
fi

log_info() { printf '%s[INFO]%s %s\n' "${BLUE}${BOLD}" "${RESET}" "$*"; }
log_ok()   { printf '%s[OK]%s %s\n' "${GREEN}${BOLD}" "${RESET}" "$*"; }
log_warn() { printf '%s[WARN]%s %s\n' "${YELLOW}${BOLD}" "${RESET}" "$*" >&2; }
log_err()  { printf '%s[ERROR]%s %s\n' "${RED}${BOLD}" "${RESET}" "$*" >&2; }

usage() {
    printf 'Usage: %s [--auto]\n' "${0##*/}"
}

AUTO_MODE=0

parse_args() {
    while (($#)); do
        case $1 in
            --auto)
                AUTO_MODE=1
                ;;
            -h|--help)
                usage
                exit 0
                ;;
            *)
                log_err "Unknown argument: $1"
                usage >&2
                exit 1
                ;;
        esac
        shift
    done
}

declare -ga ALL_CARDS=()
declare -gA CARD_VENDOR_ID
declare -gA CARD_VENDOR_LABEL
declare -gA CARD_NAME
declare -gA CARD_PCI_ADDRESS
declare -gA CARD_BY_PATH
declare -gA CARD_BOOT_VGA

declare -g SELECTED_PRIMARY_CARD=''
declare -g SELECTED_MODE=''
declare -g DEFAULT_PRIMARY_CARD=''
declare -g DEFAULT_PRIMARY_REASON=''

TEMP_OUTPUT=''

cleanup() {
    if [[ -n ${TEMP_OUTPUT:-} ]]; then
        rm -f -- "$TEMP_OUTPUT"
    fi
}
trap cleanup EXIT

install_packages() {
    local -a packages=("$@")
    (( ${#packages[@]} > 0 )) || return 0

    if (( EUID == 0 )); then
        pacman -S --needed --noconfirm "${packages[@]}"
        return
    fi

    if ! command -v sudo >/dev/null 2>&1; then
        log_err "sudo is required to install missing packages: ${packages[*]}"
        return 1
    fi

    sudo pacman -S --needed --noconfirm "${packages[@]}"
}

check_deps() {
    local -a missing=()

    if ! command -v lspci >/dev/null 2>&1; then
        missing+=('pciutils')
    fi

    (( ${#missing[@]} == 0 )) && return 0

    log_warn "Missing dependencies detected: ${missing[*]}"
    log_info "Attempting to install via pacman..."

    if ! install_packages "${missing[@]}"; then
        log_err "Failed to install required dependencies. Aborting."
        exit 1
    fi

    hash -r
    log_ok "Dependencies installed successfully."
}

vendor_label_from_id() {
    case ${1,,} in
        0x8086) printf 'Intel' ;;
        0x1002) printf 'AMD' ;;
        0x10de) printf 'NVIDIA' ;;
        *)      printf 'Vendor %s' "$1" ;;
    esac
}

get_pci_name() {
    local pci_address=$1
    local line=''
    local name=''

    if ! command -v lspci >/dev/null 2>&1; then
        printf '%s' 'Unknown PCI Device'
        return 0
    fi

    if line=$(lspci -s "$pci_address" 2>/dev/null); then
        name=$(sed -E 's/^[0-9a-fA-F:.]+ [^:]+: //' <<<"$line")
        [[ -n $name ]] && printf '%s' "$name" || printf '%s' 'Unknown PCI Device'
    else
        printf '%s' 'Unknown PCI Device'
    fi
}

sort_cards_by_pci() {
    local src_name=$1
    local dst_name=$2
    local -n src=$src_name
    local -n dst=$dst_name
    local -a rows=()
    local card=''

    if (( ${#src[@]} == 0 )); then
        dst=()
        return 0
    fi

    for card in "${src[@]}"; do
        rows+=("${CARD_PCI_ADDRESS[$card]}"$'\t'"$card")
    done

    mapfile -t dst < <(
        printf '%s\n' "${rows[@]}" |
        sort -t $'\t' -k1,1 |
        cut -f2-
    )
}

detect_topology() {
    local -a card_paths=(/sys/class/drm/card+([0-9]))
    local card_path=''
    local vendor_file=''
    local vendor_id=''
    local sys_device_path=''
    local pci_address=''
    local dev_node=''
    local human_name=''
    local boot_vga=''
    local by_path_link=''

    log_info "Scanning GPU topology via sysfs..."

    if (( ${#card_paths[@]} == 0 )); then
        log_err "No DRM card nodes found in /sys/class/drm. Is KMS enabled?"
        exit 1
    fi

    for card_path in "${card_paths[@]}"; do
        vendor_file="$card_path/device/vendor"
        [[ -r $vendor_file ]] || continue

        dev_node="/dev/dri/${card_path##*/}"
        [[ -e $dev_node ]] || continue

        if ! sys_device_path=$(readlink -f -- "$card_path/device"); then
            log_warn "Skipping unreadable DRM device path: $card_path"
            continue
        fi

        pci_address="${sys_device_path##*/}"
        vendor_id=$(<"$vendor_file")
        vendor_id=${vendor_id,,}

        boot_vga=0
        [[ -r "$card_path/device/boot_vga" ]] && boot_vga=$(<"$card_path/device/boot_vga")

        by_path_link="/dev/dri/by-path/pci-${pci_address}-card"
        human_name=$(get_pci_name "$pci_address")

        ALL_CARDS+=("$dev_node")
        CARD_VENDOR_ID["$dev_node"]="$vendor_id"
        CARD_VENDOR_LABEL["$dev_node"]="$(vendor_label_from_id "$vendor_id")"
        CARD_NAME["$dev_node"]="$human_name"
        CARD_PCI_ADDRESS["$dev_node"]="$pci_address"
        CARD_BY_PATH["$dev_node"]="$by_path_link"
        CARD_BOOT_VGA["$dev_node"]="$boot_vga"
    done

    if (( ${#ALL_CARDS[@]} == 0 )); then
        log_err "No usable GPUs detected in /sys/class/drm."
        exit 1
    fi
}

determine_default_primary() {
    local -a sorted_all=()
    local -a boot_cards=()
    local card=''

    sort_cards_by_pci ALL_CARDS sorted_all

    for card in "${sorted_all[@]}"; do
        [[ ${CARD_BOOT_VGA[$card]} == 1 ]] && boot_cards+=("$card")
    done

    case ${#boot_cards[@]} in
        0)
            DEFAULT_PRIMARY_CARD=${sorted_all[0]}
            DEFAULT_PRIMARY_REASON='No boot_vga GPU reported; defaulting to lowest PCI address'
            ;;
        1)
            DEFAULT_PRIMARY_CARD=${boot_cards[0]}
            DEFAULT_PRIMARY_REASON='Primary boot_vga hardware mapping'
            ;;
        *)
            DEFAULT_PRIMARY_CARD=${boot_cards[0]}
            DEFAULT_PRIMARY_REASON='Multiple boot_vga GPUs reported; defaulting to lowest PCI address'
            ;;
    esac
}

print_topology() {
    local -a sorted_all=()
    local card=''
    local marker=''

    sort_cards_by_pci ALL_CARDS sorted_all

    printf '\n%s--- GPU Topology Detected ---%s\n' "$BOLD" "$RESET"

    for card in "${sorted_all[@]}"; do
        marker=''
        [[ ${CARD_BOOT_VGA[$card]} == 1 ]] && marker+=" ${YELLOW}[boot_vga]${RESET}"
        [[ $card == "$DEFAULT_PRIMARY_CARD" ]] && marker+=" ${GREEN}[default]${RESET}"

        printf '  • %s%s%s%s\n' "$BOLD" "$card" "$RESET" "$marker"
        printf '      ├─ Name: %s\n' "${CARD_NAME[$card]}"
        printf '      ├─ PCI : %s\n' "${CARD_PCI_ADDRESS[$card]}"

        if [[ -e "${CARD_BY_PATH[$card]}" ]]; then
            printf '      └─ Link: %s\n' "${CARD_BY_PATH[$card]}"
        else
            printf '      └─ Link: unavailable\n'
        fi
    done

    printf '\n'
}

select_primary_gpu() {
    local -a sorted_all=()
    local card=''
    local choice=''
    local default_index=1
    local index=1
    local selected_index=0
    local marker=''

    determine_default_primary
    print_topology
    sort_cards_by_pci ALL_CARDS sorted_all

    if (( ${#sorted_all[@]} == 1 )); then
        SELECTED_PRIMARY_CARD=$DEFAULT_PRIMARY_CARD
        SELECTED_MODE='single'
        log_info "Single GPU detected; using ${SELECTED_PRIMARY_CARD}."
        return 0
    fi

    if (( AUTO_MODE == 1 )); then
        SELECTED_PRIMARY_CARD=$DEFAULT_PRIMARY_CARD
        SELECTED_MODE='auto'
        log_info "Auto-selected primary GPU based on: $DEFAULT_PRIMARY_REASON."
        return 0
    fi

    for card in "${sorted_all[@]}"; do
        [[ $card == "$DEFAULT_PRIMARY_CARD" ]] && default_index=$index
        ((index++))
    done

    printf 'Select the GPU that should drive Hyprland.\n\n'

    index=1
    for card in "${sorted_all[@]}"; do
        marker=''
        [[ $card == "$DEFAULT_PRIMARY_CARD" ]] && marker=" ${GREEN}[default]${RESET}"
        printf '  %d) %s (%s)%s\n' "$index" "$card" "${CARD_VENDOR_LABEL[$card]}" "$marker"
        printf '      %s\n' "${CARD_NAME[$card]}"
        ((index++))
    done

    printf '\n'

    if ! read -rp "Enter choice [${default_index}]: " choice; then
        printf '\n'
        log_warn "Input closed; using default selection."
        choice=$default_index
    fi

    [[ -z ${choice:-} ]] && choice=$default_index

    if [[ $choice =~ ^[0-9]+$ ]] && (( choice >= 1 && choice <= ${#sorted_all[@]} )); then
        selected_index=$((choice - 1))
        SELECTED_PRIMARY_CARD=${sorted_all[selected_index]}
    else
        log_warn "Invalid selection. Using default."
        SELECTED_PRIMARY_CARD=$DEFAULT_PRIMARY_CARD
    fi

    SELECTED_MODE='manual'
    log_info "Selected primary GPU: ${SELECTED_PRIMARY_CARD} (${CARD_NAME[$SELECTED_PRIMARY_CARD]})."
}

build_ordered_cards() {
    local primary=$1
    local dst_name=$2
    local -n dst=$dst_name
    local -a sorted_all=()
    local card=''

    sort_cards_by_pci ALL_CARDS sorted_all

    dst=("$primary")
    for card in "${sorted_all[@]}"; do
        [[ $card == "$primary" ]] && continue
        dst+=("$card")
    done
}

build_aq_runtime_string() {
    local src_name=$1
    local -n cards=$src_name
    local -a parts=()
    local card=''
    local by_path=''

    for card in "${cards[@]}"; do
        by_path=${CARD_BY_PATH[$card]}

        if [[ -e $by_path ]]; then
            # Generates: $(readlink -f -- '/dev/dri/by-path/...')
            parts+=("\$(readlink -f -- ${by_path@Q})")
        else
            log_warn "Missing by-path symlink for $card; falling back to current node name."
            parts+=("$card")
        fi
    done

    local IFS=:
    printf '%s' "${parts[*]}"
}

generate_config() {
    local -a ordered_cards=()
    local aq_runtime_string=''
    local primary_vendor_id=''

    build_ordered_cards "$SELECTED_PRIMARY_CARD" ordered_cards
    aq_runtime_string=$(build_aq_runtime_string ordered_cards)
    primary_vendor_id=${CARD_VENDOR_ID[$SELECTED_PRIMARY_CARD]}

    mkdir -p -- "$ENV_DIR"
    TEMP_OUTPUT=$(mktemp "$ENV_DIR/.gpu.XXXXXX")

    {
        printf '# -----------------------------------------------------------------\n'
        printf '# UWSM GPU Config | Mode: %s\n' "${SELECTED_MODE^^}"
        printf '# Primary DRM node: %s\n' "$SELECTED_PRIMARY_CARD"
        printf '# Primary GPU: %s | %s | %s\n' \
            "${CARD_VENDOR_LABEL[$SELECTED_PRIMARY_CARD]}" \
            "${CARD_NAME[$SELECTED_PRIMARY_CARD]}" \
            "${CARD_PCI_ADDRESS[$SELECTED_PRIMARY_CARD]}"
        printf '# -----------------------------------------------------------------\n'
        printf 'export ELECTRON_OZONE_PLATFORM_HINT=auto\n'
        printf 'export MOZ_ENABLE_WAYLAND=1\n'
        printf '\n'
        
        printf '# Hyprland / Aquamarine GPU priority\n'
        printf '# Resolved dynamically at session start to avoid colon-parsing bugs.\n'
        printf 'export AQ_DRM_DEVICES="%s"\n' "$aq_runtime_string"
        printf '\n'

        # Dynamically probe for actual drivers to prevent software-decoding fallbacks
        case ${primary_vendor_id,,} in
            0x8086)
                printf '# Intel Media Session\n'
                if [[ -e $INTEL_IHD_DRIVER ]]; then
                    printf 'export LIBVA_DRIVER_NAME=iHD\n'
                elif [[ -e $INTEL_I965_DRIVER ]]; then
                    printf 'export LIBVA_DRIVER_NAME=i965\n'
                fi
                ;;
            0x1002)
                printf '# AMD Media Session\n'
                if [[ -e $AMD_VAAPI_DRIVER ]]; then
                    printf 'export LIBVA_DRIVER_NAME=radeonsi\n'
                fi
                ;;
            0x10de)
                printf '# NVIDIA Primary Session\n'
                printf 'export GBM_BACKEND=nvidia-drm\n'
                printf 'export __GLX_VENDOR_LIBRARY_NAME=nvidia\n'
                if [[ -e $NVIDIA_VAAPI_DRIVER ]]; then
                    printf 'export LIBVA_DRIVER_NAME=nvidia\n'
                fi
                ;;
        esac

    } >"$TEMP_OUTPUT"

    chmod 0644 -- "$TEMP_OUTPUT"

    if [[ -f $OUTPUT_FILE ]] && cmp -s -- "$TEMP_OUTPUT" "$OUTPUT_FILE"; then
        rm -f -- "$TEMP_OUTPUT"
        TEMP_OUTPUT=''
        log_ok "Config is strictly optimal and up to date: $OUTPUT_FILE"
    else
        mv -f -- "$TEMP_OUTPUT" "$OUTPUT_FILE"
        TEMP_OUTPUT=''
        log_ok "Config generated and securely written to: $OUTPUT_FILE"
    fi
}

preview_config() {
    log_info "Previewing active config parameters:"
    printf '%s\n' '-------------------------------------'
    grep -E 'AQ_DRM_DEVICES|GBM_BACKEND|__GLX_VENDOR_LIBRARY_NAME|LIBVA_DRIVER_NAME|Mode:|Primary DRM node:|Primary GPU:' "$OUTPUT_FILE" || true
    printf '%s\n' '-------------------------------------'
}

main() {
    parse_args "$@"
    log_info "Starting Elite DevOps GPU Configuration..."
    check_deps
    detect_topology
    select_primary_gpu
    generate_config
    preview_config
    log_ok "Done. Please restart your UWSM session."
}

main "$@"
