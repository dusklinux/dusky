#!/usr/bin/env bash
# Bash 5.3+ | Setup Limine, AUR dependencies, and EFI sequence
set -Eeuo pipefail
export LC_ALL=C

trap 'echo -e "\n\033[1;31m[FATAL]\033[0m Script failed at line $LINENO. Command: $BASH_COMMAND" >&2' ERR

AUTO_MODE=false
[[ "${1:-}" == "--auto" ]] && AUTO_MODE=true

sudo -v || { echo "FATAL: Cannot obtain sudo privileges." >&2; exit 1; }
# Keep sudo credential alive during interactive pauses
( while true; do sudo -n -v 2>/dev/null; sleep 240; done ) &
SUDO_PID=$!
trap 'kill $SUDO_PID 2>/dev/null || true' EXIT

# Strict Pre-flight Checks
[[ -d /sys/firmware/efi ]] || { echo "FATAL: System is not booted in EFI mode." >&2; exit 1; }
[[ "$(stat -f -c %T /)" == "btrfs" ]] || { echo "FATAL: Root filesystem is not BTRFS." >&2; exit 1; }
ESP_PARTTYPE=$(lsblk -ndo PARTTYPE "$(findmnt -fno SOURCE /boot)" 2>/dev/null || echo "")
[[ "${ESP_PARTTYPE,,}" == "c12a7328-f81f-11d2-ba4b-00a0c93ec93b" ]] || { echo "FATAL: /boot is not flagged as a valid ESP (GPT Type C12A...). " >&2; exit 1; }

execute() {
    local desc="$1"
    shift
    if [[ "$AUTO_MODE" == true ]]; then
        "$@"
    else
        printf '\n\033[1;34m[ACTION]\033[0m %s\n' "$desc"
        read -rp "Execute this step? [Y/n] " response || { echo -e "\nInput closed; aborting." >&2; exit 1; }
        if [[ "${response,,}" =~ ^(n|no)$ ]]; then
            echo "Skipped."
            return 0
        fi
        "$@"
    fi
}

install_packages() {
    local aur_helper=""
    if command -v paru &>/dev/null; then aur_helper="paru";
    elif command -v yay &>/dev/null; then aur_helper="yay"; fi
    
    [[ -n "$aur_helper" ]] || { echo "FATAL: No AUR helper (yay/paru) found." >&2; return 1; }
    
    sudo pacman -S --needed --noconfirm limine efibootmgr snapper snap-pac
    "$aur_helper" -S --needed --noconfirm limine-snapper-sync
}
execute "Install core snapshot and bootloader packages" install_packages

deploy_limine() {
    [[ -f /usr/share/limine/BOOTX64.EFI ]] || { echo "FATAL: Limine EFI binary missing." >&2; return 1; }
    sudo mkdir -p /boot/EFI/BOOT
    # Best-effort safe write (FAT32 lacks atomic rename)
    sudo cp /usr/share/limine/BOOTX64.EFI /boot/EFI/BOOT/BOOTX64.EFI.tmp
    sudo sync
    sudo mv /boot/EFI/BOOT/BOOTX64.EFI.tmp /boot/EFI/BOOT/BOOTX64.EFI
    sudo sync
}
execute "Deploy Limine EFI binary to ESP" deploy_limine

generate_limine_conf() {
    local root_part mount_opts root_subvol
    root_part=$(findmnt -fno SOURCE / | sed 's/\[.*\]//')
    [[ -n "$root_part" ]] || { echo "FATAL: Could not determine root block device." >&2; return 1; }

    mount_opts=$(findmnt -fno OPTIONS /) || { echo "FATAL: findmnt failed for /." >&2; return 1; }
    root_subvol=$(echo "$mount_opts" | grep -oP 'subvol=\K[^,]+' || echo "@")

    local luks_uuid=""
    local mapper_name=""
    local kernel_cmdline="quiet loglevel=3 splash rw rootflags=subvol=${root_subvol} nowatchdog nmi_watchdog=0 mitigations=off audit=0"

    if [[ "$root_part" == /dev/mapper/* ]]; then
        mapper_name="${root_part##*/}"
        local backing_dev
        backing_dev=$(sudo cryptsetup status "$root_part" | awk '/device:/ {print $2}')
        luks_uuid=$(sudo blkid -s UUID -o value "$backing_dev" || true)
        
        [[ -n "$luks_uuid" ]] || { echo "FATAL: Could not determine LUKS UUID for $backing_dev." >&2; return 1; }
        
        # Arch mkinitcpio override semantics: last HOOKS definition wins
        local effective_hooks_line
        effective_hooks_line=$(cat /etc/mkinitcpio.conf /etc/mkinitcpio.conf.d/*.conf 2>/dev/null | grep -E '^\s*HOOKS\s*=' | tail -n1 || true)
        
        if [[ " $effective_hooks_line " == *" sd-encrypt "* ]]; then
            kernel_cmdline+=" rd.luks.name=${luks_uuid}=${mapper_name} root=/dev/mapper/${mapper_name}"
        elif [[ " $effective_hooks_line " == *" encrypt "* ]]; then
            kernel_cmdline+=" cryptdevice=UUID=${luks_uuid}:${mapper_name} root=/dev/mapper/${mapper_name}"
        else
            echo "FATAL: Root is LUKS but no encrypt/sd-encrypt hook found in active mkinitcpio configs." >&2
            return 1
        fi
    else
        local root_uuid
        root_uuid=$(sudo blkid -s UUID -o value "$root_part")
        kernel_cmdline+=" root=UUID=${root_uuid}"
    fi

    # Detect standalone microcode
    local ucode_lines=""
    local effective_ucode_hooks
    effective_ucode_hooks=$(cat /etc/mkinitcpio.conf /etc/mkinitcpio.conf.d/*.conf 2>/dev/null | grep -E '^\s*HOOKS\s*=' | tail -n1 || true)
    
    if [[ ! " $effective_ucode_hooks " == *" microcode "* ]]; then
        shopt -s nullglob
        local ucode_images=(/boot/*-ucode.img)
        shopt -u nullglob
        for img in "${ucode_images[@]}"; do
            ucode_lines+="    module_path: boot():/$(basename "$img")"$'\n'
        done
    fi

local conf_content="timeout: 5
default_entry: 1
remember_last_entry: no
hash_mismatch_panic: no

/+Arch Linux
    //linux
    protocol: linux
    kernel_path: boot():/vmlinuz-linux
${ucode_lines}    module_path: boot():/initramfs-linux.img
    cmdline: $kernel_cmdline

    //linux-previous
    protocol: linux
    kernel_path: boot():/vmlinuz-linux-previous
${ucode_lines}    module_path: boot():/initramfs-linux-previous.img
    cmdline: $kernel_cmdline"

    echo "$conf_content" | sudo tee /boot/limine.conf.tmp >/dev/null
    sudo sync
    sudo mv /boot/limine.conf.tmp /boot/limine.conf
    sudo sync
}
execute "Generate Limine Config with dynamic LUKS/Microcode logic" generate_limine_conf

register_efi() {
    local esp_source esp_pkname esp_partnum
    esp_source=$(findmnt -fno SOURCE /boot)
    esp_pkname=$(lsblk -ndo PKNAME "$esp_source")
    esp_partnum=$(cat "/sys/class/block/$(basename "$esp_source")/partition")

    [[ -n "$esp_pkname" && -n "$esp_partnum" ]] || { echo "FATAL: Could not parse ESP block topology." >&2; return 1; }

    # Idempotency: Avoid NVRAM wear if entry already exists perfectly
    local existing_entries
    existing_entries=$(sudo efibootmgr | sed -n 's/^Boot\([0-9A-Fa-f]\{4\}\)[* ] Limine$/\1/p')
    local count
    count=$(echo "$existing_entries" | grep -c . || echo 0)

    if (( count == 1 )); then
        echo "INFO: Single Limine NVRAM entry already exists. Skipping recreation."
        return 0
    fi

    # Clean orphans/duplicates
    while IFS= read -r entry; do
        [[ -n "$entry" ]] && sudo efibootmgr -b "$entry" -B
    done <<< "$existing_entries"

    sudo efibootmgr --create --disk "/dev/$esp_pkname" --part "$esp_partnum" --loader '\EFI\BOOT\BOOTX64.EFI' --label 'Limine' --unicode
}
execute "Register UEFI NVRAM Boot Entry (Idempotent)" register_efi
