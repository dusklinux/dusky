#!/usr/bin/env bash
# Arch Linux (Btrfs root) | Root & Home Snapper isolated snapshots setup
# Bash 5.3+

set -Eeuo pipefail
export LC_ALL=C
trap 'printf "\n\033[1;31m[FATAL]\033[0m Script failed at line %d. Command: %s\n" "$LINENO" "$BASH_COMMAND" >&2; trap - ERR' ERR

AUTO_MODE=false
[[ "${1:-}" == "--auto" ]] && AUTO_MODE=true

declare -A BACKED_UP=()

SUDO_PID=""

fatal() {
    printf '\033[1;31m[FATAL]\033[0m %s\n' "$1" >&2
    exit 1
}

info() {
    printf '\033[1;32m[INFO]\033[0m %s\n' "$1"
}

warn() {
    printf '\033[1;33m[WARN]\033[0m %s\n' "$1" >&2
}

cleanup() {
    kill "${SUDO_PID:-}" 2>/dev/null || true
}
trap cleanup EXIT INT TERM

execute() {
    local desc="$1"
    shift

    if [[ "$AUTO_MODE" == true ]]; then
        "$@"
        return 0
    fi

    printf '\n\033[1;34m[ACTION]\033[0m %s\n' "$desc"
    read -r -p "Execute this step? [Y/n] " response || fatal "Input closed; aborting."
    if [[ "${response,,}" =~ ^(n|no)$ ]]; then
        info "Skipped."
        return 0
    fi

    "$@"
}

backup_file() {
    local file="$1"

    [[ -e "$file" ]] || return 0
    [[ -n "${BACKED_UP["$file"]+x}" ]] && return 0

    local stamp
    stamp="$(date +%Y%m%d-%H%M%S)"

    sudo cp -a -- "$file" "${file}.bak.${stamp}"
    BACKED_UP["$file"]=1
    info "Backup created: ${file}.bak.${stamp}"
}

require_cmd() {
    command -v "$1" >/dev/null 2>&1 || fatal "Required command not found: $1"
}

extract_subvol() {
    local opts="$1"
    local opt value
    local -a parts=()

    IFS=',' read -r -a parts <<< "$opts"
    for opt in "${parts[@]}"; do
        case "$opt" in
            subvol=*)
                value="${opt#subvol=}"
                value="${value#/}"
                printf '%s\n' "$value"
                return 0
                ;;
        esac
    done
    return 1
}

strip_subvol_opts() {
    local opts="$1"
    local opt
    local -a parts=()
    local -a kept=()

    IFS=',' read -r -a parts <<< "$opts"
    for opt in "${parts[@]}"; do
        case "$opt" in
            subvol=*|subvolid=*)
                ;;
            *)
                kept+=("$opt")
                ;;
        esac
    done

    local joined=""
    if ((${#kept[@]} > 0)); then
        joined="${kept[0]}"
        local i
        for ((i = 1; i < ${#kept[@]}; i++)); do
            joined+=",${kept[i]}"
        done
    fi

    printf '%s\n' "$joined"
}

get_root_source() {
    findmnt -no SOURCE / | sed 's/\[.*\]//'
}

get_root_uuid() {
    local source uuid

    uuid="$(findmnt -no UUID / 2>/dev/null || true)"
    if [[ -n "$uuid" ]]; then
        printf '%s\n' "$uuid"
        return 0
    fi

    source="$(get_root_source)"
    [[ -n "$source" ]] || return 1

    blkid -s UUID -o value "$source" 2>/dev/null || true
}

get_root_mount_opts() {
    findmnt -no OPTIONS /
}

dir_has_entries() {
    local dir="$1"
    sudo find "$dir" -mindepth 1 -maxdepth 1 -print -quit 2>/dev/null | grep -q .
}

path_is_btrfs_subvolume() {
    local path="$1"
    sudo btrfs subvolume show "$path" >/dev/null 2>&1
}

verify_snapshots_mount() {
    local mount_target="$1"
    local expected_subvol="$2"
    local root_uuid snap_uuid mounted_opts mounted_subvol

    root_uuid="$(get_root_uuid)"
    [[ -n "$root_uuid" ]] || fatal "Could not determine the Btrfs UUID for /"

    findmnt -M "$mount_target" >/dev/null 2>&1 || fatal "${mount_target} is not mounted."

    snap_uuid="$(findmnt -M "$mount_target" -no UUID 2>/dev/null || true)"
    [[ -n "$snap_uuid" ]] || fatal "Could not determine the filesystem UUID for ${mount_target}"
    [[ "$snap_uuid" == "$root_uuid" ]] || fatal "${mount_target} is mounted from a different filesystem."

    mounted_opts="$(findmnt -M "$mount_target" -no OPTIONS 2>/dev/null || true)"
    mounted_subvol="$(extract_subvol "$mounted_opts" || true)"
    mounted_subvol="${mounted_subvol#/}"

    [[ "$mounted_subvol" == "$expected_subvol" ]] || fatal "${mount_target} is mounted, but not from subvol=/${expected_subvol}"

    sudo chmod 750 "$mount_target"
    info "${mount_target} is mounted from ${expected_subvol}"
}

install_packages() {
    sudo pacman -S --needed --noconfirm snapper btrfs-progs
}

post_install_checks() {
    require_cmd btrfs
    require_cmd snapper
    require_cmd systemctl
}

ensure_snapper_config() {
    local config_name="$1"
    local config_path="$2"

    if sudo snapper -c "$config_name" get-config >/dev/null 2>&1; then
        info "Snapper ${config_name} config already exists."
        return 0
    fi

    if mountpoint -q "${config_path}/.snapshots"; then
        fatal "Snapper ${config_name} config is missing, but ${config_path}/.snapshots is already a mountpoint."
    fi

    sudo snapper -c "$config_name" create-config "$config_path"
    sudo snapper -c "$config_name" get-config >/dev/null 2>&1 || fatal "Snapper ${config_name} config was not created correctly."
    info "Created Snapper ${config_name} config."
}

ensure_top_level_snapshots_subvolume() {
    local subvol_target="$1"
    local root_source tmp_mnt mounted=false

    root_source="$(get_root_source)"
    [[ -n "$root_source" ]] || fatal "Could not determine the root source device."

    tmp_mnt="$(mktemp -d)"
    cleanup_top_level_mount() {
        if [[ "$mounted" == true ]]; then
            sudo umount "$tmp_mnt" 2>/dev/null || true
        fi
        rmdir "$tmp_mnt" 2>/dev/null || true
    }
    trap cleanup_top_level_mount RETURN

    sudo mount -o subvolid=5 "$root_source" "$tmp_mnt"
    mounted=true

    if [[ -e "${tmp_mnt}/${subvol_target}" ]]; then
        if sudo btrfs subvolume show "${tmp_mnt}/${subvol_target}" >/dev/null 2>&1; then
            info "Top-level subvolume ${subvol_target} already exists."
        else
            fatal "Top-level path ${subvol_target} exists, but it is not a Btrfs subvolume."
        fi
    else
        sudo btrfs subvolume create "${tmp_mnt}/${subvol_target}"
        info "Created top-level subvolume ${subvol_target}."
    fi

    trap - RETURN
    cleanup_top_level_mount
}

prepare_snapshots_mountpoint() {
    local mount_target="$1"
    local expected_subvol="$2"

    sudo mkdir -p "$mount_target"

    if mountpoint -q "$mount_target"; then
        verify_snapshots_mount "$mount_target" "$expected_subvol"
        return 0
    fi

    if [[ ! -d "$mount_target" ]]; then
        fatal "${mount_target} exists, but it is not a directory."
    fi

    if path_is_btrfs_subvolume "$mount_target"; then
        if dir_has_entries "$mount_target"; then
            fatal "Nested ${mount_target} is a populated Btrfs subvolume. Refusing destructive migration."
        fi

        sudo btrfs subvolume delete "$mount_target"
        sudo mkdir -p "$mount_target"
        info "Removed empty nested ${mount_target} subvolume."
        return 0
    fi

    if dir_has_entries "$mount_target"; then
        fatal "${mount_target} is a non-empty directory. Refusing to mount over existing contents."
    fi
}

ensure_fstab_entry_for_snapshots() {
    local mount_target="$1"
    local subvol_target="$2"
    local fs_uuid root_opts cleaned_opts mount_opts newline tmp

    fs_uuid="$(get_root_uuid)"
    [[ -n "$fs_uuid" ]] || fatal "Could not determine the Btrfs UUID for /"

    root_opts="$(get_root_mount_opts)"
    cleaned_opts="$(strip_subvol_opts "$root_opts")"

    mount_opts="$cleaned_opts"
    [[ -n "$mount_opts" ]] && mount_opts+=","
    mount_opts+="subvol=/${subvol_target}"

    newline="UUID=${fs_uuid} ${mount_target} btrfs ${mount_opts} 0 0"

    backup_file /etc/fstab
    tmp="$(mktemp)"

    awk -v mp="$mount_target" -v newline="$newline" '
        BEGIN { done = 0 }
        /^[[:space:]]*#/ { print; next }
        NF >= 2 && $2 == mp {
            if (!done) {
                print newline
                done = 1
            }
            next
        }
        { print }
        END {
            if (!done) {
                print newline
            }
        }
    ' /etc/fstab > "$tmp"

    sudo install -m 0644 "$tmp" /etc/fstab
    rm -f "$tmp"

    sudo systemctl daemon-reload
    info "Ensured ${mount_target} entry in /etc/fstab"
}

mount_snapshots() {
    local mount_target="$1"
    local expected_subvol="$2"

    sudo mkdir -p "$mount_target"

    if mountpoint -q "$mount_target"; then
        verify_snapshots_mount "$mount_target" "$expected_subvol"
        return 0
    fi

    sudo mount "$mount_target"
    verify_snapshots_mount "$mount_target" "$expected_subvol"
}

verify_snapper_works() {
    local config_name="$1"
    sudo snapper -c "$config_name" get-config >/dev/null 2>&1 || fatal "Snapper ${config_name} config is not usable."
    sudo snapper -c "$config_name" list >/dev/null 2>&1 || fatal "Snapper cannot access the ${config_name} snapshot set."
    info "Snapper ${config_name} config is working."
}

tune_snapper() {
    local config_name="$1"
    sudo snapper -c "$config_name" set-config \
        TIMELINE_CREATE="no" \
        NUMBER_CLEANUP="yes" \
        NUMBER_LIMIT="10" \
        NUMBER_LIMIT_IMPORTANT="5" \
        SPACE_LIMIT="0.0" \
        FREE_LIMIT="0.0"

    sudo btrfs quota disable / 2>/dev/null || true
    info "Applied Snapper retention settings for ${config_name}."
}

preflight_checks() {
    (( EUID != 0 )) || fatal "Run this script as a regular user with sudo privileges, not as root."

    require_cmd sudo
    require_cmd pacman
    require_cmd findmnt
    require_cmd mountpoint
    require_cmd awk
    require_cmd sed
    require_cmd grep
    require_cmd stat
    require_cmd mktemp
    require_cmd date

    [[ "$(stat -f -c %T /)" == "btrfs" ]] || fatal "Root filesystem is not Btrfs."
    [[ "$(stat -f -c %T /home)" == "btrfs" ]] || fatal "/home is not Btrfs."

    sudo -v || fatal "Cannot obtain sudo privileges."
    (
        while true; do
            sudo -n -v 2>/dev/null || exit
            sleep 240
        done
    ) &
    SUDO_PID=$!
}

preflight_checks

execute "Install Snapper packages" install_packages
post_install_checks

# --- ROOT SNAPSHOT CONFIG ---
execute "Create Snapper root config" ensure_snapper_config "root" "/"
execute "Create top-level @snapshots subvolume" ensure_top_level_snapshots_subvolume "@snapshots"
execute "Prepare /.snapshots mountpoint safely" prepare_snapshots_mountpoint "/.snapshots" "@snapshots"
execute "Write /.snapshots mount to /etc/fstab" ensure_fstab_entry_for_snapshots "/.snapshots" "@snapshots"
execute "Mount /.snapshots from @snapshots" mount_snapshots "/.snapshots" "@snapshots"
execute "Verify Snapper can use the root snapshot layout" verify_snapper_works "root"
execute "Apply Snapper cleanup settings (root)" tune_snapper "root"

# --- HOME SNAPSHOT CONFIG ---
execute "Create Snapper home config" ensure_snapper_config "home" "/home"
execute "Create top-level @home_snapshots subvolume" ensure_top_level_snapshots_subvolume "@home_snapshots"
execute "Prepare /home/.snapshots mountpoint safely" prepare_snapshots_mountpoint "/home/.snapshots" "@home_snapshots"
execute "Write /home/.snapshots mount to /etc/fstab" ensure_fstab_entry_for_snapshots "/home/.snapshots" "@home_snapshots"
execute "Mount /home/.snapshots from @home_snapshots" mount_snapshots "/home/.snapshots" "@home_snapshots"
execute "Verify Snapper can use the home snapshot layout" verify_snapper_works "home"
execute "Apply Snapper cleanup settings (home)" tune_snapper "home"
