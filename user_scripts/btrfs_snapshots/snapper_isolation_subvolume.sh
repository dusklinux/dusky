#!/usr/bin/env bash
# Arch Linux (Btrfs root) | Root & Home Snapper isolated snapshots setup
# Bash 5.3+

set -Eeuo pipefail
export LC_ALL=C

AUTO_MODE=false
[[ "${1:-}" == "--auto" ]] && AUTO_MODE=true

declare -A BACKED_UP=()
declare -A CACHE_MNT_SOURCE=()
declare -A CACHE_MNT_UUID=()
declare -A CACHE_MNT_OPTS=()

declare -a ACTIVE_TEMP_MOUNTS=()
declare -a ACTIVE_TEMP_FILES=()
declare -a ROLLBACK_CMDS=()
SUDO_PID=""

cleanup() {
    local cmd mnt f
    if (( ${#ROLLBACK_CMDS[@]} > 0 )); then
        warn "Executing transactional rollbacks..."
        for cmd in "${ROLLBACK_CMDS[@]}"; do eval "$cmd" 2>/dev/null || true; done
    fi
    for mnt in "${ACTIVE_TEMP_MOUNTS[@]}"; do
        if mountpoint -q "$mnt"; then sudo umount "$mnt" 2>/dev/null || true; fi
        rmdir "$mnt" 2>/dev/null || true
    done
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
    local stamp; printf -v stamp '%(%Y%m%d-%H%M%S)T' -1
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
    [[ -v CACHE_MNT_SOURCE["$target"] ]] && return 0
    
    local findmnt_out source uuid opts fstab_opts
    findmnt_out="$(findmnt -n -e -o SOURCE,UUID,OPTIONS -M "$target" 2>/dev/null || true)"
    [[ -n "$findmnt_out" ]] || fatal "Could not determine mount info for $target"
    
    read -r source uuid opts <<< "$findmnt_out"
    source="${source%%\[*}"
    
    fstab_opts="$(findmnt -s -n -e -o OPTIONS -M "$target" 2>/dev/null || true)"
    [[ -n "$fstab_opts" ]] && opts="$fstab_opts"

    if [[ -z "$uuid" || "$uuid" == "-" ]]; then
        uuid="$(sudo blkid -s UUID -o value "$source" 2>/dev/null || true)"
    fi
    [[ -n "$uuid" ]] || fatal "Could not determine UUID for $target"
    
    CACHE_MNT_SOURCE["$target"]="$source"
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

clean_mount_opts() {
    local opts="$1" opt
    local -a parts kept
    IFS=',' read -r -a parts <<< "$opts"
    for opt in "${parts[@]}"; do
        case "$opt" in subvol=*|subvolid=*|ro) continue ;; *) kept+=("$opt") ;; esac
    done
    if (( ${#kept[@]} > 0 )); then
        local IFS=,
        printf '%s\n' "${kept[*]}"
    fi
}

dir_is_empty() {
    sudo rmdir "$1" 2>/dev/null && { sudo mkdir "$1"; return 0; }
    return 1
}

path_is_btrfs_subvolume() { sudo btrfs subvolume show "$1" >/dev/null 2>&1; }

verify_snapshots_mount() {
    local mount_target="$1" expected_subvol="$2" base_target="$3" target_uuid
    load_mount_info "$base_target"
    target_uuid="${CACHE_MNT_UUID["$base_target"]}"

    findmnt -M "$mount_target" >/dev/null 2>&1 || fatal "${mount_target} is not mounted."

    local snap_info snap_uuid mounted_opts mounted_subvol
    snap_info="$(findmnt -n -e -o UUID,OPTIONS -M "$mount_target" 2>/dev/null || true)"
    read -r snap_uuid mounted_opts <<< "$snap_info"
    
    [[ "$snap_uuid" == "$target_uuid" ]] || fatal "${mount_target} filesystem UUID mismatch."
    mounted_subvol="$(extract_subvol "$mounted_opts" || true)"
    [[ "${mounted_subvol#/}" == "${expected_subvol#/}" ]] || fatal "${mount_target} subvol mismatch."

    sudo chmod 750 "$mount_target"
    info "${mount_target} is mounted correctly."
}

install_packages() { sudo pacman -S --needed --noconfirm snapper btrfs-progs; }
post_install_checks() { require_cmd btrfs; require_cmd snapper; require_cmd systemctl; }

ensure_snapper_config() {
    local config_name="$1" config_path="$2"
    sudo snapper -c "$config_name" get-config >/dev/null 2>&1 && { info "Snapper ${config_name} exists."; return 0; }
    mountpoint -q "${config_path}/.snapshots" && fatal "${config_path}/.snapshots is already a mountpoint."

    sudo snapper -c "$config_name" create-config "$config_path"
    ROLLBACK_CMDS+=("sudo snapper -c ${config_name} delete-config")
    info "Created Snapper ${config_name} config."
}

ensure_top_level_snapshots_subvolume() {
    local base_path="$1" subvol_target="$2" root_source root_opts tmp_mnt extra_opts="subvolid=5"
    load_mount_info "$base_path"
    root_source="${CACHE_MNT_SOURCE["$base_path"]}"
    root_opts="${CACHE_MNT_OPTS["$base_path"]}"

    [[ ",$root_opts," == *",degraded,"* ]] && extra_opts+=",degraded"

    tmp_mnt="$(mktemp -d)"
    ACTIVE_TEMP_MOUNTS+=("$tmp_mnt")
    sudo mount -o "$extra_opts" "$root_source" "$tmp_mnt" || fatal "Mount failed."

    if [[ -e "${tmp_mnt}/${subvol_target}" ]]; then
        path_is_btrfs_subvolume "${tmp_mnt}/${subvol_target}" || fatal "${subvol_target} exists but is not a subvol."
    else
        sudo btrfs subvolume create "${tmp_mnt}/${subvol_target}" >/dev/null
        info "Created top-level subvolume ${subvol_target}."
    fi

    sudo umount "$tmp_mnt"; rmdir "$tmp_mnt"
    ACTIVE_TEMP_MOUNTS=("${ACTIVE_TEMP_MOUNTS[@]/$tmp_mnt}")
}

prepare_snapshots_mountpoint() {
    local mount_target="$1"
    [[ -L "$mount_target" ]] && fatal "Symlink detected."
    sudo mkdir -p "$mount_target"
    mountpoint -q "$mount_target" && return 0

    if path_is_btrfs_subvolume "$mount_target"; then
        dir_is_empty "$mount_target" || fatal "Populated nested subvolume found."
        sudo btrfs subvolume delete "$mount_target" >/dev/null
        sudo mkdir -p "$mount_target"
        info "Removed empty nested subvol."
        return 0
    fi
    dir_is_empty "$mount_target" || fatal "Directory not empty."
}

ensure_fstab_entry_for_snapshots() {
    local base_path="$1" mount_target="$2" subvol_target="$3"
    local fs_uuid base_opts mount_opts newline tmp canonical_target

    load_mount_info "$base_path"
    fs_uuid="${CACHE_MNT_UUID["$base_path"]}"
    base_opts="${CACHE_MNT_OPTS["$base_path"]}"

    mount_opts="$(clean_mount_opts "$base_opts")"
    [[ -n "$mount_opts" ]] && mount_opts+=","
    mount_opts+="subvol=/${subvol_target#/}"

    canonical_target="$(realpath -m "$mount_target")"
    newline="UUID=${fs_uuid} ${canonical_target} btrfs ${mount_opts} 0 0"

    tmp="$(mktemp)"
    ACTIVE_TEMP_FILES+=("$tmp")

    awk -v mp="$canonical_target" -v newline="$newline" '
        BEGIN { done = 0 }
        /^[[:space:]]*#/ { print; next }
        NF >= 2 { sub(/\/+$/, "", $2); if ($2 == mp) { if (!done) { print newline; done = 1 }; next } }
        { print }
        END { if (!done) print newline }
    ' /etc/fstab > "$tmp"

    if ! findmnt --verify --tab-file "$tmp" >/dev/null 2>&1; then
        fatal "Generated fstab failed libmount validation."
    fi

    backup_file /etc/fstab
    atomic_write /etc/fstab "$tmp"
    rm -f "$tmp"; ACTIVE_TEMP_FILES=("${ACTIVE_TEMP_FILES[@]/$tmp}")
    sudo systemctl daemon-reload
    info "Ensured entry in /etc/fstab"
}

mount_snapshots() {
    local mount_target="$1" expected_subvol="$2" base_target="$3"
    sudo mkdir -p "$mount_target"
    mountpoint -q "$mount_target" || sudo mount "$mount_target"
    verify_snapshots_mount "$mount_target" "$expected_subvol" "$base_target"
    ROLLBACK_CMDS=() 
}

verify_snapper_works() {
    sudo snapper -c "$1" list >/dev/null 2>&1 || fatal "Snapper $1 config is broken."
}

tune_snapper() {
    sudo snapper -c "$1" set-config TIMELINE_CREATE="no" NUMBER_CLEANUP="yes" NUMBER_LIMIT="10" NUMBER_LIMIT_IMPORTANT="5" SPACE_LIMIT="0.0" FREE_LIMIT="0.0"
}

quiesce_snapper() {
    if systemctl is-active --quiet snapper-timeline.timer || systemctl is-active --quiet snapper-cleanup.timer; then
        sudo systemctl stop snapper-timeline.timer snapper-cleanup.timer 2>/dev/null || true
    fi
}

preflight_checks() {
    (( EUID != 0 )) || fatal "Run as regular user with sudo."
    require_cmd sudo; require_cmd pacman; require_cmd findmnt; require_cmd awk; require_cmd realpath; require_cmd grep; require_cmd stat; require_cmd mktemp
    [[ "$(stat -f -c %T /)" == "btrfs" ]] || fatal "Root is not Btrfs."
    [[ "$(stat -f -c %T /home)" == "btrfs" ]] || fatal "/home is not Btrfs."
    sudo -v || fatal "Cannot obtain sudo privileges."
    (while true; do sudo -n -v 2>/dev/null || exit; sleep 240; done) &
    SUDO_PID=$!
}

preflight_checks; quiesce_snapper; execute "Install Snapper" install_packages; post_install_checks

# --- ROOT SNAPSHOT CONFIG ---
execute "Create Snapper root" ensure_snapper_config "root" "/"
execute "Create top-level @snapshots" ensure_top_level_snapshots_subvolume "/" "@snapshots"
execute "Prepare /.snapshots" prepare_snapshots_mountpoint "/.snapshots"
execute "Write /.snapshots to fstab" ensure_fstab_entry_for_snapshots "/" "/.snapshots" "@snapshots"
execute "Mount /.snapshots" mount_snapshots "/.snapshots" "@snapshots" "/"
execute "Verify Snapper root" verify_snapper_works "root"
execute "Tune Snapper root" tune_snapper "root"

# --- HOME SNAPSHOT CONFIG ---
execute "Create Snapper home" ensure_snapper_config "home" "/home"
execute "Create top-level @home_snapshots" ensure_top_level_snapshots_subvolume "/home" "@home_snapshots"
execute "Prepare /home/.snapshots" prepare_snapshots_mountpoint "/home/.snapshots"
execute "Write /home/.snapshots to fstab" ensure_fstab_entry_for_snapshots "/home" "/home/.snapshots" "@home_snapshots"
execute "Mount /home/.snapshots" mount_snapshots "/home/.snapshots" "@home_snapshots" "/home"
execute "Verify Snapper home" verify_snapper_works "home"
execute "Tune Snapper home" tune_snapper "home"

execute "Apply Global Btrfs Settings" apply_global_btrfs_tuning() { sudo btrfs quota disable / 2>/dev/null || true; }
