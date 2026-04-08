#!/usr/bin/env bash
# ==============================================================================
# MODULE: 007_limine_bootloader.sh
# CONTEXT: Arch chroot environment
# PURPOSE: Dynamic Limine Deployment (UEFI/BIOS, LUKS/Plain, CachyOS/Vanilla)
# ==============================================================================

set -euo pipefail

# --- Visual Constants ---
readonly C_BOLD=$'\033[1m'
readonly C_RED=$'\033[31m'
readonly C_GREEN=$'\033[32m'
readonly C_YELLOW=$'\033[33m'
readonly C_CYAN=$'\033[36m'
readonly C_RESET=$'\033[0m'

AUTO_MODE=0

log_info() { printf "${C_CYAN}[INFO]${C_RESET} %s\n" "$1"; }
log_success() { printf "${C_GREEN}[SUCCESS]${C_RESET} %s\n" "$1"; }
log_warn() { printf "${C_YELLOW}[WARN]${C_RESET} %s\n" "$1"; }
log_error() { printf "${C_RED}[ERROR]${C_RESET} %s\n" "$1"; }

ask_proceed() {
    local prompt_text="$1"
    if (( AUTO_MODE )); then return 0; fi
    local response
    read -r -p "${C_BOLD}${prompt_text} [Y/n] ${C_RESET}" response
    [[ "$response" =~ ^([yY][eE][sS]|[yY]|"")$ ]]
}

resolve_single_disk_ancestor() {
    local node=$1 path type
    local -A seen=()
    local -a disks=()

    while read -r path type; do
        [[ $type == disk ]] || continue
        [[ -n ${seen[$path]+x} ]] && continue
        seen["$path"]=1
        disks+=("$path")
    done < <(lsblk -nrspo PATH,TYPE -s -- "$node")

    case ${#disks[@]} in
        1) printf '%s\n' "${disks[0]}" ;;
        0) return 1 ;;
        *) return 2 ;;
    esac
}

detect_luks_ancestor() {
    lsblk -nrspo PATH,TYPE -s -- "$1" | awk '$2 == "crypt" { print $1; exit }'
}

dm_name_from_node() {
    local real_path dm_node dm_name

    if ! real_path=$(readlink -f -- "$1"); then return 1; fi
    dm_node=${real_path##*/}

    if [[ ! -r "/sys/class/block/${dm_node}/dm/name" ]]; then return 1; fi
    if ! IFS= read -r dm_name < "/sys/class/block/${dm_node}/dm/name"; then return 1; fi
    
    printf '%s\n' "$dm_name"
}

detect_crypto_cmdline_style() {
    local hooks_str=""

    # Securely evaluate mkinitcpio.conf AND any modern drop-ins natively
    if hooks_str=$(bash -c '
        source /etc/mkinitcpio.conf >/dev/null 2>&1 || true
        shopt -s nullglob
        for conf in /etc/mkinitcpio.conf.d/*.conf; do
            source "$conf" >/dev/null 2>&1 || true
        done
        echo "${HOOKS[*]}"
    ' 2>/dev/null); then
        if [[ " $hooks_str " == *" sd-encrypt "* ]]; then
            printf '%s\n' 'rd.luks'
            return 0
        fi
        if [[ " $hooks_str " == *" encrypt "* ]]; then
            printf '%s\n' 'cryptdevice'
            return 0
        fi
    fi

    if command -v dracut >/dev/null 2>&1 || [[ -d /usr/lib/dracut ]]; then
        printf '%s\n' 'rd.luks'
        return 0
    fi

    return 1
}

# ==============================================================================
# ENTRY LOGIC
# ==============================================================================

if [[ "${1:-}" == "--auto" || "${1:-}" == "auto" ]]; then
    AUTO_MODE=1
else
    read -r -p "Run AUTONOMOUS bootloader deployment? [y/N]: " choice
    if [[ "${choice,,}" == "y" ]]; then
        AUTO_MODE=1
    fi
fi

printf '%b\n\n' "${C_BOLD}=== DYNAMIC BOOTLOADER ORCHESTRATION ===${C_RESET}"

if (( EUID != 0 )); then
    log_error "This script must be run as root within the arch-chroot."
    exit 1
fi

log_info "Ensuring necessary bootloader packages..."
pacman -S --needed --noconfirm limine efibootmgr awk sed >/dev/null

# Detect Boot Mode
if [[ -d /sys/firmware/efi/efivars ]]; then
    readonly BOOT_MODE="UEFI"
else
    readonly BOOT_MODE="BIOS"
fi
log_info "Detected Boot Mode: ${C_BOLD}${BOOT_MODE}${C_RESET}"

# --- Dynamic Block Topology Traversal ---
log_info "Analyzing filesystem topology..."

RAW_ROOT_MNT=$(findmnt -n -e -o SOURCE -T /)
ROOT_BLK_DEV="${RAW_ROOT_MNT%%\[*}"

ROOT_UUID=$(findmnt -n -e -o UUID -T / || true)
if [[ -z "$ROOT_UUID" || "$ROOT_UUID" == "-" ]]; then
    ROOT_UUID=$(blkid -s UUID -o value "$ROOT_BLK_DEV")
fi
if [[ -z "$ROOT_UUID" ]]; then
    log_error "Could not resolve the root filesystem UUID."
    exit 1
fi

ROOT_OPTS=$(findmnt -n -e -o OPTIONS -T /)
ROOT_SUBVOL=""
if [[ "$ROOT_OPTS" == *subvol=* ]]; then
    ROOT_SUBVOL=$(echo "$ROOT_OPTS" | grep -oP '(?<=subvol=)[^,]+' || true)
fi

CMDLINE_BASE="rw quiet splash nowatchdog"
ROOT_SPEC="root=UUID=${ROOT_UUID}"
CRYPT_DEV=$(detect_luks_ancestor "$ROOT_BLK_DEV" || true)

if [[ -n "$CRYPT_DEV" ]]; then
    log_info "LUKS encryption detected in the root-device stack."

    if ! MAPPER_NAME=$(dm_name_from_node "$CRYPT_DEV"); then
        log_error "Could not resolve the dm-crypt mapping name for ${CRYPT_DEV}."
        exit 1
    fi

    BACKING_DEV=$(cryptsetup status "$MAPPER_NAME" | awk '/^[[:space:]]*device:/ { print $2; exit }')
    if [[ -z "$BACKING_DEV" ]]; then
        log_error "Could not determine the backing block device for encrypted root."
        exit 1
    fi

    LUKS_UUID=$(blkid -s UUID -o value "$BACKING_DEV")
    if [[ -z "$LUKS_UUID" ]]; then
        log_error "Could not determine the LUKS UUID for encrypted root."
        exit 1
    fi

    if ! CRYPTO_STYLE=$(detect_crypto_cmdline_style); then
        log_error "Encrypted root detected, but the initramfs crypto unlock syntax could not be determined."
        log_warn "Please ensure your mkinitcpio.conf includes the 'encrypt' or 'sd-encrypt' hook."
        exit 1
    fi

    case "$CRYPTO_STYLE" in
        rd.luks)
            CMDLINE_BASE="rd.luks.name=${LUKS_UUID}=${MAPPER_NAME} rd.luks.options=discard ${ROOT_SPEC} ${CMDLINE_BASE}"
            ;;
        cryptdevice)
            CMDLINE_BASE="cryptdevice=UUID=${LUKS_UUID}:${MAPPER_NAME}:allow-discards ${ROOT_SPEC} ${CMDLINE_BASE}"
            ;;
    esac
else
    log_info "No LUKS layer detected in the root-device stack."
    CMDLINE_BASE="${ROOT_SPEC} ${CMDLINE_BASE}"
fi

if [[ -n "$ROOT_SUBVOL" ]]; then
    CMDLINE_BASE="${CMDLINE_BASE} rootflags=subvol=${ROOT_SUBVOL}"
fi

log_info "Generated Kernel Params: ${C_YELLOW}${CMDLINE_BASE}${C_RESET}"

ask_proceed "Ready to deploy bootloader binaries. Continue?" || exit 0

# --- Limine Binary Deployment ---
if [[ "$BOOT_MODE" == "UEFI" ]]; then
    ESP_MNT=""
    for target in /efi /boot /boot/efi; do
        if mountpoint -q "$target" && [[ "$(findmnt -n -o FSTYPE "$target")" =~ ^(vfat|fat32)$ ]]; then
            ESP_MNT="$target"
            break
        fi
    done

    if [[ -z "$ESP_MNT" ]]; then
        log_error "Could not locate EFI System Partition (vfat). Ensure it is mounted."
        exit 1
    fi

    log_info "Deploying Limine EFI binary to ESP at $ESP_MNT..."
    install -Dm0644 /usr/share/limine/BOOTX64.EFI "${ESP_MNT}/EFI/BOOT/BOOTX64.EFI"

    ESP_SRC=$(findmnt -n -o SOURCE "$ESP_MNT")
    ESP_DISK=$(lsblk -no PKNAME -- "$ESP_SRC")
    ESP_PART_NUM=$(lsblk -no PARTN -- "$ESP_SRC")

    if [[ -n "$ESP_DISK" && -n "$ESP_PART_NUM" ]]; then
        log_info "Registering EFI NVRAM Entry on /dev/${ESP_DISK} (Partition ${ESP_PART_NUM})..."
        efibootmgr --create --disk "/dev/${ESP_DISK}" --part "$ESP_PART_NUM" --loader '\EFI\BOOT\BOOTX64.EFI' --label 'Limine' --unicode >/dev/null || log_warn "NVRAM update failed (normal in some VMs)."
    else
        log_warn "Could not resolve the ESP disk/partition for NVRAM registration; skipping efibootmgr."
    fi
else
    BOOT_FS_SRC=$(findmnt -n -e -o SOURCE -T /boot)
    BOOT_BLK_DEV="${BOOT_FS_SRC%%\[*}"

    resolve_status=0
    BOOT_DISK=$(resolve_single_disk_ancestor "$BOOT_BLK_DEV") || resolve_status=$?

    case "$resolve_status" in
        0) ;;
        1)
            log_error "Could not resolve the BIOS install target disk from ${BOOT_BLK_DEV}."
            exit 1
            ;;
        2)
            log_error "The boot filesystem resolves to multiple disks; refusing to guess a BIOS install target."
            exit 1
            ;;
    esac

    log_info "Deploying Limine BIOS stage file to /boot..."
    install -Dm0644 /usr/share/limine/limine-bios.sys /boot/limine-bios.sys

    log_info "Deploying Limine BIOS boot sector to ${BOOT_DISK}..."
    limine bios-install "${BOOT_DISK}"
fi

# --- Configuration Generation (Vanilla vs CachyOS) ---
CONF_FILE="/boot/limine.conf"

if command -v limine-entry-tool >/dev/null 2>&1; then
    log_info "CachyOS Environment Detected: Leveraging limine-entry-tool..."

    mkdir -p /etc/kernel
    printf '%s\n' "$CMDLINE_BASE" > /etc/kernel/cmdline

    limine-entry-tool >/dev/null
    log_success "Dynamic configuration generated via limine-entry-tool."
else
    log_info "Vanilla Arch Environment Detected: Generating dynamic static config..."

    if ! shopt -q nullglob; then
        shopt -s nullglob
        nullglob_reset=1
    else
        nullglob_reset=0
    fi

    kernels=(/boot/vmlinuz-*)
    (( nullglob_reset == 1 )) && shopt -u nullglob || true

    if (( ${#kernels[@]} == 0 )); then
        log_error "No kernels were found under /boot; refusing to write an empty Limine configuration."
        exit 1
    fi

    cat <<EOF > "$CONF_FILE"
timeout: 5
default_entry: 1
remember_last_entry: yes

EOF

    for kernel_path in "${kernels[@]}"; do
        KNAME=$(basename "$kernel_path" | sed 's/^vmlinuz-//')
        INITRAMFS_PATH="/boot/initramfs-${KNAME}.img"
        FALLBACK_PATH="/boot/initramfs-${KNAME}-fallback.img"

        echo "/Arch Linux ($KNAME)" >> "$CONF_FILE"
        echo "    protocol: linux" >> "$CONF_FILE"
        echo "    kernel_path: boot():/vmlinuz-${KNAME}" >> "$CONF_FILE"
        echo "    cmdline: ${CMDLINE_BASE}" >> "$CONF_FILE"

        if [[ -f "$INITRAMFS_PATH" ]]; then
            echo "    module_path: boot():/initramfs-${KNAME}.img" >> "$CONF_FILE"
        fi
        echo "" >> "$CONF_FILE"

        if [[ -f "$FALLBACK_PATH" ]]; then
            echo "/Arch Linux ($KNAME - Fallback)" >> "$CONF_FILE"
            echo "    protocol: linux" >> "$CONF_FILE"
            echo "    kernel_path: boot():/vmlinuz-${KNAME}" >> "$CONF_FILE"

            FALLBACK_CMD=$(printf '%s\n' "$CMDLINE_BASE" | sed -E 's/\b(quiet|splash)\b//g' | tr -s ' ')

            echo "    cmdline: ${FALLBACK_CMD}" >> "$CONF_FILE"
            echo "    module_path: boot():/initramfs-${KNAME}-fallback.img" >> "$CONF_FILE"
            echo "" >> "$CONF_FILE"
        fi
    done
    log_success "Base configuration generated at $CONF_FILE."
fi

# --- Lifecycle Hooks ---
if [[ "$BOOT_MODE" == "UEFI" ]]; then
    log_info "Installing Pacman ALPM hook for Limine EFI updates..."
    mkdir -p /etc/pacman.d/hooks
    
    # Note: Pacman hooks strictly require a single line for the 'Exec' key.
    cat <<'EOF' > /etc/pacman.d/hooks/limine-update.hook
[Trigger]
Type = Package
Operation = Install
Operation = Upgrade
Target = limine

[Action]
Description = Deploying updated Limine EFI binary to the mounted ESP...
When = PostTransaction
Exec = /usr/bin/sh -eu -c 'for target in /efi /boot /boot/efi; do if mountpoint -q "$target"; then case "$(findmnt -n -o FSTYPE "$target" 2>/dev/null || true)" in vfat|fat32) install -Dm0644 /usr/share/limine/BOOTX64.EFI "$target/EFI/BOOT/BOOTX64.EFI"; exit 0;; esac; fi; done; echo "limine-update.hook: no mounted ESP found" >&2; exit 1'
EOF
else
    rm -f /etc/pacman.d/hooks/limine-update.hook
fi

log_success "Bootloader Orchestration Complete."
exit 0
