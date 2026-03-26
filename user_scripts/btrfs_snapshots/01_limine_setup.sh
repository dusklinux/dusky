#!/usr/bin/env bash
# Arch Linux (EFI + Btrfs root) | Limine core setup
# Bash 5.3+

# --- USER CONFIGURATION ---
# Set the path to your custom Limine wallpaper here.
# Note: Limine STRICTLY supports PNG, JPEG, and BMP formats.
# Example: LIMINE_WALLPAPER_SOURCE="/home/user/Pictures/splash.png"
LIMINE_WALLPAPER_SOURCE=""
# --------------------------

set -Eeuo pipefail
export LC_ALL=C

AUTO_MODE=false
[[ "${1:-}" == "--auto" ]] && AUTO_MODE=true

declare -A BACKED_UP=()
declare -a EFFECTIVE_HOOKS=()
declare -A EFFECTIVE_HOOKS_SET=()

declare -A CACHE_MNT_SOURCE=()
declare -A CACHE_MNT_UUID=()
declare -A CACHE_MNT_OPTS=()

declare -a ACTIVE_TEMP_FILES=()

SUDO_PID=""
NEEDS_LIMINE_UPDATE=false
CACHE_ESP_PATH=""
CACHE_ESP_PARTUUID=""
CACHE_EFIBOOTMGR_OUTPUT=""

cleanup() {
    local f
    for f in "${ACTIVE_TEMP_FILES[@]}"; do
        [[ -n "$f" && -f "$f" ]] && sudo rm -f "$f" 2>/dev/null || true
    done
    kill "${SUDO_PID:-}" 2>/dev/null || true
}

trap_exit() { cleanup; }
trap_interrupt() { cleanup; printf '\n\033[1;31m[FATAL]\033[0m Script interrupted.\n' >&2; exit 130; }
trap 'printf "\n\033[1;31m[FATAL]\033[0m Script failed at line %d. Command: %s\n" "$LINENO" "$BASH_COMMAND" >&2; cleanup' ERR
trap trap_exit EXIT
trap trap_interrupt INT TERM HUP

fatal() { printf '\033[1;31m[FATAL]\033[0m %s\n' "$1" >&2; exit 1; }
info() { printf '\033[1;32m[INFO]\033[0m %s\n' "$1"; }
warn() { printf '\033[1;33m[WARN]\033[0m %s\n' "$1" >&2; }

execute() {
    local desc="$1"; shift
    if [[ "$AUTO_MODE" == true ]]; then "$@"; return 0; fi
    printf '\n\033[1;34m[ACTION]\033[0m %s\n' "$desc"
    read -r -p "Execute this step? [Y/n] " response || fatal "Input closed; aborting."
    if [[ "${response,,}" =~ ^(n|no)$ ]]; then info "Skipped."; return 0; fi
    "$@"
}

backup_file() {
    local file="$1"
    [[ -e "$file" ]] || return 0
    [[ -v BACKED_UP["$file"] ]] && return 0

    local stamp
    printf -v stamp '%(%Y%m%d-%H%M%S)T' -1
    sudo cp -a -- "$file" "${file}.bak.${stamp}"
    BACKED_UP["$file"]=1
    info "Backup created: ${file}.bak.${stamp}"
}

require_cmd() {
    command -v "$1" >/dev/null 2>&1 || fatal "Required command not found: $1"
}

atomic_write() {
    local target="$1" src="$2" target_dir tmp_target
    target_dir="$(dirname "$target")"
    tmp_target="$(sudo mktemp "${target_dir}/.tmp.XXXXXX")"
    ACTIVE_TEMP_FILES+=("$tmp_target")

    sudo cp "$src" "$tmp_target"
    sudo chmod 0644 "$tmp_target"
    sudo mv "$tmp_target" "$target"

    ACTIVE_TEMP_FILES=("${ACTIVE_TEMP_FILES[@]/$tmp_target}")
    sudo sync -f "$target_dir" 2>/dev/null || true
}

load_mount_info() {
    local target="$1"
    [[ -v CACHE_MNT_OPTS["$target"] ]] && return 0

    local findmnt_out source uuid opts
    findmnt_out="$(findmnt -n -e -o SOURCE,UUID,OPTIONS -M "$target" 2>/dev/null || true)"
    [[ -n "$findmnt_out" ]] || fatal "Could not determine mount info for $target"

    read -r source uuid opts <<< "$findmnt_out"
    CACHE_MNT_SOURCE["$target"]="${source%%\[*}"
    CACHE_MNT_UUID["$target"]="$uuid"
    CACHE_MNT_OPTS["$target"]="$opts"
}

extract_subvol() {
    if [[ "$1" =~ subvol=([^,]+) ]]; then
        printf '%s\n' "${BASH_REMATCH[1]#/}"
        return 0
    fi
    return 1
}

get_root_subvolume_path() {
    load_mount_info "/"
    local path
    path="$(extract_subvol "${CACHE_MNT_OPTS["/"]}" || true)"
    [[ -n "$path" ]] && { printf '%s\n' "$path"; return 0; }

    require_cmd btrfs
    path="$(sudo btrfs subvolume show / 2>/dev/null | sed -n 's/^[[:space:]]*Path:[[:space:]]*//p' || true)"
    path="${path#/}"
    case "$path" in ""|"<FS_TREE>"|"/") return 1 ;; esac
    printf '%s\n' "$path"
}

build_btrfs_rootflags() {
    local opts="$1" root_subvol="$2" opt
    local -a parts=() flags=()
    [[ -n "$root_subvol" ]] && flags+=("subvol=/${root_subvol#/}")

    IFS=',' read -r -a parts <<< "$opts"
    for opt in "${parts[@]}"; do
        case "$opt" in rw|ro|subvol=*|subvolid=*) continue ;; *) flags+=("$opt") ;; esac
    done

    if ((${#flags[@]} > 0)); then
        local IFS=,
        printf '%s' "${flags[*]}"
    fi
}

collect_mkinitcpio_files() {
    local -a files=("/etc/mkinitcpio.conf")
    local file shopt_save
    shopt_save=$(shopt -p nullglob || true)
    shopt -s nullglob
    for file in /etc/mkinitcpio.conf.d/*.conf; do
        [[ "$file" == "/etc/mkinitcpio.conf.d/zz-limine-overlayfs.conf" ]] && continue
        files+=("$file")
    done
    eval "$shopt_save"
    printf '%s\n' "${files[@]}"
}

get_effective_hooks() {
    local -a files=()
    mapfile -t files < <(collect_mkinitcpio_files)

    EFFECTIVE_HOOKS=()
    EFFECTIVE_HOOKS_SET=()

    local hooks_str
    hooks_str="$(
        env -i PATH="$PATH" LC_ALL=C bash -c '
            set -e
            for f in "$@"; do
                [[ -f "$f" ]] || continue
                source "$f" >/dev/null 2>&1
            done
            [[ "$(declare -p HOOKS 2>/dev/null)" =~ "declare -a" ]] || HOOKS=(${HOOKS})
            for h in "${HOOKS[@]}"; do echo "$h"; done
        ' bash "${files[@]}"
    )"

    [[ -n "$hooks_str" ]] || fatal "Could not determine the effective mkinitcpio HOOKS array."
    mapfile -t EFFECTIVE_HOOKS <<< "$hooks_str"
    local hook
    for hook in "${EFFECTIVE_HOOKS[@]}"; do EFFECTIVE_HOOKS_SET["$hook"]=1; done
}

hook_present() { [[ -v EFFECTIVE_HOOKS_SET["$1"] ]]; }

detect_esp_mountpoint() {
    [[ -n "$CACHE_ESP_PATH" ]] && { printf '%s\n' "$CACHE_ESP_PATH"; return 0; }
    if command -v bootctl >/dev/null 2>&1; then
        local esp
        esp="$(bootctl --print-esp-path 2>/dev/null || true)"
        if [[ -n "$esp" && -d "$esp" ]]; then
            CACHE_ESP_PATH="$esp"
            printf '%s\n' "$CACHE_ESP_PATH"
            return 0
        fi
    fi

    local candidate fstype
    for candidate in /efi /boot /boot/efi; do
        if mountpoint -q "$candidate"; then
            fstype="$(findmnt -M "$candidate" -no FSTYPE 2>/dev/null || true)"
            case "$fstype" in
                vfat|fat|msdos)
                    CACHE_ESP_PATH="$candidate"
                    printf '%s\n' "$CACHE_ESP_PATH"
                    return 0
                    ;;
            esac
        fi
    done
    return 1
}

get_mount_partuuid() {
    local mountpoint="$1" source
    [[ -n "$CACHE_ESP_PARTUUID" ]] && { printf '%s\n' "$CACHE_ESP_PARTUUID"; return 0; }
    source="$(findmnt -M "$mountpoint" -no SOURCE 2>/dev/null || true)"
    [[ -n "$source" ]] || return 1
    CACHE_ESP_PARTUUID="$(sudo blkid -s PARTUUID -o value "$source" 2>/dev/null || true)"
    printf '%s\n' "$CACHE_ESP_PARTUUID"
}

set_shell_var() {
    local file="$1" key="$2" value="$3" escaped_value
    escaped_value="${value//\\/\\\\}"
    escaped_value="${escaped_value//&/\\&}"
    escaped_value="${escaped_value//|/\\|}"
    sudo touch "$file"
    if sudo grep -qE "^[[:space:]]*${key}=" "$file"; then
        sudo sed -i -E "s|^[[:space:]]*${key}=.*|${key}=\"${escaped_value}\"|" "$file"
    else
        printf '%s="%s"\n' "$key" "$value" | sudo tee -a "$file" >/dev/null
    fi
}

dep_satisfied() {
    local missing
    missing="$(pacman -T "$1" 2>/dev/null || true)"
    [[ -z "$missing" ]]
}

choose_java_provider() {
    local pkg
    for pkg in jdk-openjdk jdk21-openjdk jdk25-openjdk; do
        if pacman -Si "$pkg" >/dev/null 2>&1; then
            printf '%s\n' "$pkg"
            return 0
        fi
    done
    return 1
}

ensure_aur_build_prereqs() {
    local need_java=false dep provider
    for dep in 'java-runtime>=21' 'java-environment>=21'; do
        if ! dep_satisfied "$dep"; then
            need_java=true
            break
        fi
    done
    [[ "$need_java" == true ]] || return 0
    provider="$(choose_java_provider)" || fatal "A Java provider for java-environment>=21 is required."
    info "Installing $provider to satisfy Java build dependencies."
    sudo pacman -S --needed --noconfirm "$provider"
}

install_kernel_headers_if_needed() {
    local has_dkms=false moddir pkgbase headers_pkg shopt_save
    pacman -Q dkms >/dev/null 2>&1 && has_dkms=true
    shopt_save=$(shopt -p nullglob || true)
    shopt -s nullglob
    local dkms_dirs=(/var/lib/dkms/*)
    ((${#dkms_dirs[@]} > 0)) && has_dkms=true
    eval "$shopt_save"
    [[ "$has_dkms" == true ]] || return 0

    moddir="/usr/lib/modules/$(uname -r)"
    [[ ! -r "${moddir}/pkgbase" ]] && return 0
    pkgbase="$(<"${moddir}/pkgbase")"
    headers_pkg="${pkgbase}-headers"

    pacman -Q "$headers_pkg" >/dev/null 2>&1 && return 0
    if pacman -Si "$headers_pkg" >/dev/null 2>&1; then
        info "DKMS detected; installing matching kernel headers: $headers_pkg"
        sudo pacman -S --needed --noconfirm "$headers_pkg"
    fi
}

install_repo_packages() {
    sudo pacman -S --needed --noconfirm limine efibootmgr kernel-modules-hook btrfs-progs
    install_kernel_headers_if_needed
}

load_efibootmgr_cache() {
    [[ -z "$CACHE_EFIBOOTMGR_OUTPUT" ]] && CACHE_EFIBOOTMGR_OUTPUT="$(sudo efibootmgr -v 2>/dev/null || true)"
}

get_boot_entries_for_loader_on_esp() {
    local loader_path="$1" esp_partuuid="${2:-}" line entry_code line_lc loader_lc partuuid_lc
    loader_lc="${loader_path,,}"
    partuuid_lc="${esp_partuuid,,}"
    load_efibootmgr_cache

    while IFS= read -r line; do
        [[ "$line" =~ ^Boot([0-9A-Fa-f]{4})\*?[[:space:]] ]] || continue
        entry_code="${BASH_REMATCH[1]^^}"
        line_lc="${line,,}"
        [[ -n "$partuuid_lc" && "$line_lc" != *"gpt,${partuuid_lc},"* ]] && continue
        [[ "$line_lc" == *"$loader_lc"* ]] && printf '%s\n' "$entry_code"
    done <<< "$CACHE_EFIBOOTMGR_OUTPUT"
}

has_loader_entry_on_esp() {
    local -a entries=()
    mapfile -t entries < <(get_boot_entries_for_loader_on_esp "$1" "${2:-}")
    ((${#entries[@]} > 0))
}

get_primary_canonical_limine_entry() {
    local -a entries=()
    mapfile -t entries < <(get_boot_entries_for_loader_on_esp '\EFI\limine\limine_x64.efi' "${1:-}")
    ((${#entries[@]} > 0)) || return 1
    printf '%s\n' "${entries[0]}"
}

get_limine_fallback_entries_on_esp() {
    local esp_partuuid="${1:-}" line entry_code line_lc partuuid_lc fallback_loader
    fallback_loader='\efi\boot\bootx64.efi'
    partuuid_lc="${esp_partuuid,,}"
    load_efibootmgr_cache

    while IFS= read -r line; do
        [[ "$line" =~ ^Boot([0-9A-Fa-f]{4})\*?[[:space:]] ]] || continue
        entry_code="${BASH_REMATCH[1]^^}"
        line_lc="${line,,}"
        [[ -n "$partuuid_lc" && "$line_lc" != *"gpt,${partuuid_lc},"* ]] && continue
        [[ "$line_lc" == *"$fallback_loader"* ]] || continue
        [[ "$line_lc" == *"limine"* ]] || continue
        printf '%s\n' "$entry_code"
    done <<< "$CACHE_EFIBOOTMGR_OUTPUT"
}

delete_boot_entries() {
    local entry rc=0 deleted=0
    for entry in "$@"; do
        [[ -n "$entry" ]] || continue
        deleted=1
        if ! sudo efibootmgr -b "$entry" -B >/dev/null 2>&1; then
            warn "Could not delete Boot${entry}."
            rc=1
        fi
    done
    (( deleted == 1 )) && CACHE_EFIBOOTMGR_OUTPUT=""
    return "$rc"
}

purge_limine_fallback_entries() {
    local esp_partuuid="${1:-}"
    local -a entries=() remaining=()

    mapfile -t entries < <(get_limine_fallback_entries_on_esp "$esp_partuuid")
    ((${#entries[@]} == 0)) && return 0

    warn "Deleting existing Limine fallback NVRAM entries to avoid duplicate-label warnings."
    delete_boot_entries "${entries[@]}" || true

    mapfile -t remaining < <(get_limine_fallback_entries_on_esp "$esp_partuuid")
    if ((${#remaining[@]} == 0)); then
        info "Removed Limine fallback NVRAM entries."
    else
        warn "One or more Limine fallback NVRAM entries remain; duplicate-label warnings may persist on this firmware."
    fi
}

dedupe_canonical_limine_entries() {
    local esp_partuuid="${1:-}" keep
    local -a entries=()

    mapfile -t entries < <(get_boot_entries_for_loader_on_esp '\EFI\limine\limine_x64.efi' "$esp_partuuid")
    ((${#entries[@]} > 1)) || return 0

    keep="${entries[0]}"
    warn "Multiple canonical Limine NVRAM entries found. Keeping Boot${keep} and deleting extras."
    delete_boot_entries "${entries[@]:1}" || true
}

get_boot_order_entries() {
    load_efibootmgr_cache
    local order
    order="$(awk -F': ' '/^BootOrder:/ { print $2; exit }' <<< "$CACHE_EFIBOOTMGR_OUTPUT")"
    [[ -n "$order" ]] || return 1
    order="${order//[[:space:]]/}"
    local -a entries=()
    IFS=',' read -r -a entries <<< "$order"
    printf '%s\n' "${entries[@]}"
}

ensure_boot_entry_first_in_order() {
    local wanted="$1"
    [[ -n "$wanted" ]] || return 0

    local -a current_order=() new_order=()
    local entry new_order_str
    local -A seen=()

    mapfile -t current_order < <(get_boot_order_entries || true)

    if ((${#current_order[@]} > 0)) && [[ "${current_order[0]^^}" == "${wanted^^}" ]]; then
        return 0
    fi

    wanted="${wanted^^}"
    new_order+=("$wanted")
    seen["$wanted"]=1

    for entry in "${current_order[@]}"; do
        entry="${entry^^}"
        [[ -n "$entry" ]] || continue
        [[ -v seen["$entry"] ]] && continue
        seen["$entry"]=1
        new_order+=("$entry")
    done

    local IFS=,
    new_order_str="${new_order[*]}"

    if sudo efibootmgr -o "$new_order_str" >/dev/null 2>&1; then
        CACHE_EFIBOOTMGR_OUTPUT=""
        info "Set BootOrder to prefer Boot${wanted}."
    else
        warn "Could not update BootOrder to prefer Boot${wanted}."
    fi
}

prepare_limine_nvram_for_install() {
    local esp_target esp_partuuid
    esp_target="$(detect_esp_mountpoint 2>/dev/null || true)"
    [[ -n "$esp_target" ]] || return 0
    esp_partuuid="$(get_mount_partuuid "$esp_target" || true)"
    purge_limine_fallback_entries "$esp_partuuid"
}

limine_state_appears_current() {
    local esp_target loader_path

    esp_target="$(detect_esp_mountpoint 2>/dev/null || true)"
    [[ -n "$esp_target" ]] || return 1
    loader_path="${esp_target}/EFI/limine/limine_x64.efi"

    [[ -f /boot/limine.conf ]] || return 1
    [[ -f "$loader_path" ]] || return 1

    [[ ! -f /etc/kernel/cmdline || /boot/limine.conf -nt /etc/kernel/cmdline ]] || return 1
    [[ ! -f /etc/default/limine || /boot/limine.conf -nt /etc/default/limine ]] || return 1

    return 0
}

install_aur_packages() {
    pacman -Q limine-mkinitcpio-hook >/dev/null 2>&1 && return 0
    if ! command -v paru >/dev/null 2>&1 && ! command -v yay >/dev/null 2>&1; then
        fatal "No supported AUR helper found."
    fi

    ensure_aur_build_prereqs
    prepare_limine_nvram_for_install

    if command -v paru >/dev/null 2>&1; then
        paru -S --needed --noconfirm --skipreview limine-mkinitcpio-hook
    else
        yay -S --needed --noconfirm --answerdiff None --answerclean None --answeredit None limine-mkinitcpio-hook
    fi

    CACHE_EFIBOOTMGR_OUTPUT=""

    if limine_state_appears_current; then
        NEEDS_LIMINE_UPDATE=false
    fi
}

get_crypt_ancestor() {
    local source="$1" path type
    while read -r path type; do
        [[ "$type" == "crypt" ]] && { printf '%s\n' "$path"; return 0; }
    done < <(lsblk -s -n -o PATH,TYPE "$source" 2>/dev/null || true)
    return 1
}

configure_cmdline() {
    require_cmd btrfs
    load_mount_info "/"

    local root_source="${CACHE_MNT_SOURCE["/"]}"
    local mount_opts="${CACHE_MNT_OPTS["/"]}"
    local root_type root_subvol rootflags root_mode
    local crypt_source mapper_name backing_dev luks_uuid root_uuid tmp img
    local -a ucode_imgs=() cmdline_parts=()

    get_effective_hooks

    root_type="$(lsblk -no TYPE "$root_source" 2>/dev/null | head -n1 || true)"
    [[ -n "$root_type" ]] || fatal "Could not determine block device type."

    root_uuid="${CACHE_MNT_UUID["/"]}"
    [[ -n "$root_uuid" && "$root_uuid" != "-" ]] || fatal "Could not determine root filesystem UUID."

    root_subvol="$(get_root_subvolume_path || true)"
    rootflags="$(build_btrfs_rootflags "$mount_opts" "$root_subvol")"

    root_mode="rw"
    [[ ",${mount_opts}," == *",ro,"* ]] && root_mode="ro"

    cmdline_parts+=("${root_mode}" "rootfstype=btrfs")
    [[ -n "$rootflags" ]] && cmdline_parts+=("rootflags=${rootflags}")

    crypt_source=""
    if [[ "$root_type" == "crypt" ]]; then
        crypt_source="$root_source"
    else
        crypt_source="$(get_crypt_ancestor "$root_source" || true)"
    fi

    if [[ -n "$crypt_source" ]]; then
        require_cmd cryptsetup
        mapper_name="${crypt_source##*/}"
        backing_dev="$(sudo cryptsetup status "$crypt_source" 2>/dev/null | awk '/device:/ { print $2; exit }' || true)"
        [[ -n "$backing_dev" ]] || fatal "Root depends on dm-crypt, but backing device could not be determined."
        luks_uuid="$(sudo blkid -s UUID -o value "$backing_dev" 2>/dev/null || true)"
        [[ -n "$luks_uuid" ]] || fatal "Could not determine LUKS UUID for $backing_dev."

        if hook_present sd-encrypt; then
            cmdline_parts+=("rd.luks.name=${luks_uuid}=${mapper_name}")
        elif hook_present encrypt; then
            cmdline_parts+=("cryptdevice=UUID=${luks_uuid}:${mapper_name}")
        else
            fatal "Root depends on dm-crypt, but no encrypt hook found in mkinitcpio."
        fi

        if [[ "$root_source" == "$crypt_source" ]]; then
            cmdline_parts+=("root=/dev/mapper/${mapper_name}")
        else
            cmdline_parts+=("root=UUID=${root_uuid}")
        fi
    else
        cmdline_parts+=("root=UUID=${root_uuid}")
    fi

    if ! hook_present microcode; then
        local shopt_save
        shopt_save=$(shopt -p nullglob || true)
        shopt -s nullglob
        ucode_imgs=(/boot/*-ucode.img)
        eval "$shopt_save"
        for img in "${ucode_imgs[@]}"; do
            cmdline_parts+=("initrd=/$(basename "$img")")
        done
    fi

    [[ -n "${EXTRA_KERNEL_CMDLINE:-}" ]] && cmdline_parts+=("${EXTRA_KERNEL_CMDLINE}")

    sudo mkdir -p /etc/kernel
    tmp="$(mktemp)"
    ACTIVE_TEMP_FILES+=("$tmp")
    printf '%s\n' "${cmdline_parts[*]}" > "$tmp"

    if ! sudo cmp -s "$tmp" /etc/kernel/cmdline 2>/dev/null; then
        backup_file /etc/kernel/cmdline
        atomic_write /etc/kernel/cmdline "$tmp"
        info "Updated /etc/kernel/cmdline"
        NEEDS_LIMINE_UPDATE=true
    fi
    rm -f "$tmp"
    ACTIVE_TEMP_FILES=("${ACTIVE_TEMP_FILES[@]/$tmp}")
}

configure_limine_defaults() {
    local limine_defaults="/etc/default/limine" esp_target current_esp

    if [[ -f /etc/limine-entry-tool.conf && ! -f "$limine_defaults" ]]; then
        sudo install -m 0644 /etc/limine-entry-tool.conf "$limine_defaults"
    else
        sudo touch "$limine_defaults"
    fi

    esp_target="$(detect_esp_mountpoint)" || fatal "Could not detect a mounted ESP."
    current_esp="$(grep -E '^[[:space:]]*ESP_PATH=' "$limine_defaults" 2>/dev/null | cut -d= -f2 | tr -d '"' || true)"

    if [[ "$current_esp" != "$esp_target" ]]; then
        backup_file "$limine_defaults"
        set_shell_var "$limine_defaults" ESP_PATH "$esp_target"
        info "Configured ESP_PATH=${esp_target}"
        NEEDS_LIMINE_UPDATE=true
    fi

    # Explicitly force Snapshots to the bottom of the boot order
    if ! sudo grep -qE '^[[:space:]]*BOOT_ORDER=' "$limine_defaults" 2>/dev/null; then
        backup_file "$limine_defaults"
        set_shell_var "$limine_defaults" BOOT_ORDER "*, *lts, *fallback, Snapshots"
        info "Configured BOOT_ORDER to prioritize kernels over Snapshots."
        NEEDS_LIMINE_UPDATE=true
    fi
}

deploy_limine() {
    local esp_target esp_partuuid canonical_present=false canonical_entry=""

    esp_target="$(detect_esp_mountpoint)" || fatal "Could not detect ESP mount."
    esp_partuuid="$(get_mount_partuuid "$esp_target" || true)"

    purge_limine_fallback_entries "$esp_partuuid"

    if has_loader_entry_on_esp '\EFI\limine\limine_x64.efi' "$esp_partuuid"; then
        canonical_present=true
    fi

    if [[ ! -f "${esp_target}/EFI/limine/limine_x64.efi" || "$canonical_present" == false ]]; then
        purge_limine_fallback_entries "$esp_partuuid"
        info "Installing Limine EFI entry."
        
        # Safely attempt UEFI fallback if standard install fails on buggy firmware
        if ! sudo limine-install; then
            warn "Standard limine-install failed. Attempting UEFI fallback installation..."
            if sudo limine-install --skip-uefi --fallback; then
                info "Fallback installation successful. Updating /etc/default/limine overrides."
                set_shell_var "/etc/default/limine" SKIP_UEFI "yes"
                set_shell_var "/etc/default/limine" ENABLE_LIMINE_FALLBACK "yes"
            else
                fatal "Fallback limine-install also failed."
            fi
        fi
        
        CACHE_EFIBOOTMGR_OUTPUT=""
        NEEDS_LIMINE_UPDATE=true
    fi

    if [[ "$NEEDS_LIMINE_UPDATE" == true || ! -f /boot/limine.conf ]]; then
        info "Refreshing Limine configuration..."
        sudo limine-update
    fi

    purge_limine_fallback_entries "$esp_partuuid"
    dedupe_canonical_limine_entries "$esp_partuuid"

    canonical_entry="$(get_primary_canonical_limine_entry "$esp_partuuid" || true)"
    if [[ -n "$canonical_entry" ]]; then
        ensure_boot_entry_first_in_order "$canonical_entry"
    else
        warn "No canonical Limine NVRAM entry found; BootOrder left unchanged."
    fi

    [[ -f /boot/limine.conf ]] || fatal "/boot/limine.conf was not created."
    info "Limine deployment completed."
}

apply_limine_theme() {
    local conf="/boot/limine.conf"
    [[ -f "$conf" ]] || return 0

    local theme_marker="# --- UI Theme ---"
    if ! sudo grep -qF "$theme_marker" "$conf"; then
        local tmp
        tmp="$(mktemp)"
        ACTIVE_TEMP_FILES+=("$tmp")
        
        cat <<EOF > "$tmp"
$theme_marker
term_palette: 1e1e2e;f38ba8;a6e3a1;f9e2af;89b4fa;f5c2e7;94e2d5;cdd6f4
term_palette_bright: 585b70;f38ba8;a6e3a1;f9e2af;89b4fa;f5c2e7;94e2d5;cdd6f4
term_background: 00000000
term_foreground: cdd6f4
term_background_bright: 00000000
term_foreground_bright: cdd6f4
EOF

        if [[ -n "${LIMINE_WALLPAPER_SOURCE:-}" && -f "$LIMINE_WALLPAPER_SOURCE" ]]; then
            local ext="${LIMINE_WALLPAPER_SOURCE##*.}"
            ext="${ext,,}"
            if [[ "$ext" =~ ^(png|jpg|jpeg|bmp)$ ]]; then
                local esp_target
                esp_target="$(detect_esp_mountpoint 2>/dev/null || true)"
                if [[ -n "$esp_target" ]]; then
                    local wp_dest="${esp_target}/limine-wallpaper.${ext}"
                    sudo cp "$LIMINE_WALLPAPER_SOURCE" "$wp_dest"
                    echo "wallpaper: boot():/limine-wallpaper.${ext}" >> "$tmp"
                    echo "wallpaper_style: stretched" >> "$tmp"
                    info "Installed Limine wallpaper to $wp_dest"
                fi
            else
                warn "Wallpaper must be PNG, JPEG, or BMP format. Skipping wallpaper injection."
            fi
        fi
        
        echo "# ----------------" >> "$tmp"
        echo "" >> "$tmp"
        
        sudo cat "$conf" >> "$tmp"
        backup_file "$conf"
        atomic_write "$conf" "$tmp"
        info "Applied Catppuccin theme and wallpaper to Limine configuration."
    fi
}

preflight_checks() {
    (( EUID != 0 )) || fatal "Run as regular user with sudo privileges."
    require_cmd sudo
    require_cmd pacman
    require_cmd findmnt
    require_cmd blkid
    require_cmd lsblk
    require_cmd awk
    require_cmd sed
    require_cmd grep
    require_cmd cmp
    require_cmd mktemp
    [[ -d /sys/firmware/efi ]] || fatal "Not booted in EFI mode."
    [[ -f /etc/mkinitcpio.conf ]] || fatal "/etc/mkinitcpio.conf not found."
    [[ "$(stat -f -c %T /)" == "btrfs" ]] || fatal "Root is not Btrfs."
    sudo -v || fatal "Cannot obtain sudo."
    (while true; do sudo -n -v 2>/dev/null || exit; sleep 240; done) &
    SUDO_PID=$!
}

preflight_checks
execute "Install Limine packages" install_repo_packages
execute "Generate /etc/kernel/cmdline" configure_cmdline
execute "Configure /etc/default/limine" configure_limine_defaults
execute "Install limine-mkinitcpio-hook" install_aur_packages
require_cmd limine-install
require_cmd limine-update
execute "Deploy Limine" deploy_limine
execute "Apply Limine UI Theme & Wallpaper" apply_limine_theme
