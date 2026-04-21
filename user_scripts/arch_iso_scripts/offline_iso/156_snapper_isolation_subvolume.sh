#!/usr/bin/env bash
# Arch Linux (Btrfs root) | Root & Home Snapper isolated snapshots setup
# CHROOT DEPLOYMENT EDITION

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
ROLLBACK_ON_EXIT=false

cleanup() {
    local cmd mnt f
    if [[ "$ROLLBACK_ON_EXIT" == true ]] && (( ${#ROLLBACK_CMDS[@]} > 0 )); then
        warn "Executing transactional rollbacks..."
        for cmd in "${ROLLBACK_CMDS[@]}"; do
            eval "$cmd" 2>/dev/null || true
        done
    fi

    for mnt in "${ACTIVE_TEMP_MOUNTS[@]}"; do
        [[ -n "$mnt" ]] || continue
        if mountpoint -q "$mnt"; then
            umount "$mnt" 2>/dev/null || true
        fi
        rmdir "$mnt" 2>/dev/null || true
    done

    for f in "${ACTIVE_TEMP_FILES[@]}"; do
        [[ -n "$f" && -f "$f" ]] && rm -f "$f" 2>/dev/null || true
    done
}

trap_exit() { cleanup; }
trap_interrupt() { ROLLBACK_ON_EXIT=true; cleanup; printf '\n\033[1;31m[FATAL]\033[0m Script interrupted.\n' >&2; exit 130; }
trap 'ROLLBACK_ON_EXIT=true; printf "\n\033[1;31m[FATAL]\033[0m Script failed at line %d. Command: %s\n" "$LINENO" "$BASH_COMMAND" >&2; cleanup' ERR
trap trap_exit EXIT
trap trap_interrupt INT TERM HUP

fatal() { ROLLBACK_ON_EXIT=true; printf '\033[1;31m[FATAL]\033[0m %s\n' "$1" >&2; exit 1; }
info() { printf '\033[1;32m[INFO]\033[0m %s\n' "$1"; }
warn() { printf '\033[1;33m[WARN]\033[0m %s\n' "$1" >&2; }

execute() {
    local desc="$1"; shift
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
    [[ -v BACKED_UP["$file"] ]] && return 0

    local stamp
    printf -v stamp '%(%Y%m%d-%H%M%S)T' -1
    cp -a -- "$file" "${file}.bak.${stamp}"
    BACKED_UP["$file"]=1
    info "Backup created: ${file}.bak.${stamp}"
}

require_cmd() {
    command -v "$1" >/dev/null 2>&1 || fatal "Required command not found: $1"
}

remove_array_value() {
    local array_name="$1" value="$2" item
    local -n arr_ref="$array_name"
    local -a new_arr=()

    for item in "${arr_ref[@]}"; do
        [[ -n "$item" && "$item" != "$value" ]] && new_arr+=("$item")
    done

    arr_ref=("${new_arr[@]}")
}

path_exists() { test -e "$1"; }
path_is_dir() { test -d "$1"; }

atomic_write() {
    local target="$1" src="$2" target_dir tmp_target
    target_dir="$(dirname "$target")"
    tmp_target="$(mktemp "${target_dir}/.tmp.XXXXXX")"
    ACTIVE_TEMP_FILES+=("$tmp_target")

    cp "$src" "$tmp_target"
    chmod 0644 "$tmp_target"
    mv "$tmp_target" "$target"

    remove_array_value ACTIVE_TEMP_FILES "$tmp_target"
    sync -f "$target_dir" 2>/dev/null || true
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
        uuid="$(blkid -s UUID -o value "$source" 2>/dev/null || true)"
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

get_mount_subvolume_path() {
    local target="$1" path
    load_mount_info "$target"

    path="$(extract_subvol "${CACHE_MNT_OPTS["$target"]}" || true)"
    if [[ -n "$path" ]]; then
        printf '%s\n' "${path#/}"
        return 0
    fi

    require_cmd btrfs
    path="$(btrfs subvolume show "$target" 2>/dev/null | sed -n 's/^[[:space:]]*Path:[[:space:]]*//p' || true)"
    path="${path#/}"
    case "$path" in
        ""|"<FS_TREE>"|"/") return 1 ;;
    esac
    printf '%s\n' "$path"
}

clean_mount_opts() {
    local opts="$1" opt
    local -a parts kept=()

    IFS=',' read -r -a parts <<< "$opts"
    for opt in "${parts[@]}"; do
        case "$opt" in
            subvol=*|subvolid=*|ro) continue ;;
            *) kept+=("$opt") ;;
        esac
    done

    if (( ${#kept[@]} > 0 )); then
        local IFS=,
        printf '%s\n' "${kept[*]}"
    fi
}

dir_is_empty() {
    test -d "$1" || return 0
    local entries
    entries="$(find "$1" -mindepth 1 -maxdepth 1 -print -quit 2>/dev/null || true)"
    [[ -z "$entries" ]]
}

path_is_btrfs_subvolume() {
    btrfs subvolume show "$1" >/dev/null 2>&1
}

btrfs_subvolume_is_ro() {
    local out
    out="$(btrfs property get -ts "$1" ro 2>/dev/null | awk -F= '{gsub(/[[:space:]]/, "", $2); print $2; exit}' || true)"
    [[ "$out" == "true" ]]
}

mount_top_level_for_base() {
    local base_path="$1" root_source root_opts tmp_mnt extra_opts="subvolid=5"
    load_mount_info "$base_path"
    root_source="${CACHE_MNT_SOURCE["$base_path"]}"
    root_opts="${CACHE_MNT_OPTS["$base_path"]}"

    [[ ",$root_opts," == *",degraded,"* ]] && extra_opts+=",degraded"

    tmp_mnt="$(mktemp -d)"
    ACTIVE_TEMP_MOUNTS+=("$tmp_mnt")
    mount -o "$extra_opts" "$root_source" "$tmp_mnt" || fatal "Mount failed."

    printf '%s\n' "$tmp_mnt"
}

release_temp_mount() {
    local tmp_mnt="$1"
    [[ -n "$tmp_mnt" ]] || return 0

    if mountpoint -q "$tmp_mnt"; then
        umount "$tmp_mnt" 2>/dev/null || true
    fi
    rmdir "$tmp_mnt" 2>/dev/null || true
    remove_array_value ACTIVE_TEMP_MOUNTS "$tmp_mnt"
}

current_snapshots_mount_matches_expected() {
    local mount_target="$1" expected_subvol="$2" base_target="$3" target_uuid
    local snap_info snap_uuid mounted_opts mounted_subvol

    load_mount_info "$base_target"
    target_uuid="${CACHE_MNT_UUID["$base_target"]}"

    findmnt -M "$mount_target" >/dev/null 2>&1 || return 1

    snap_info="$(findmnt -n -e -o UUID,OPTIONS -M "$mount_target" 2>/dev/null || true)"
    read -r snap_uuid mounted_opts <<< "$snap_info"

    [[ "$snap_uuid" == "$target_uuid" ]] || return 1
    mounted_subvol="$(extract_subvol "$mounted_opts" || true)"
    [[ "${mounted_subvol#/}" == "${expected_subvol#/}" ]]
}

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

    chmod 750 "$mount_target"
    info "${mount_target} is mounted correctly."
}

install_packages() {
    info "Reinstalling Snapper runtime packages for maximum reliability..."
    pacman -S --noconfirm snapper boost-libs btrfs-progs
    command -v ldconfig >/dev/null 2>&1 && ldconfig
}

verify_snapper_runtime() {
    # CHROOT FIX: Inject --no-dbus
    snapper --no-dbus --help >/dev/null 2>&1 || fatal "snapper is installed but not runnable. This usually indicates a package/runtime mismatch."
}

post_install_checks() {
    require_cmd btrfs
    require_cmd snapper
    require_cmd systemctl
    verify_snapper_runtime
}

ensure_snapper_config() {
    local config_name="$1" config_path="$2"
    local snap_dir="${config_path}/.snapshots"
    snap_dir="${snap_dir//\/\//\/}"

    # CHROOT FIX: Inject --no-dbus
    if snapper --no-dbus -c "$config_name" get-config >/dev/null 2>&1; then
        info "Snapper ${config_name} exists."
        return 0
    fi

    if mountpoint -q "$snap_dir"; then
        warn "${snap_dir} is already mounted. Temporarily unmounting to allow Snapper to initialize..."
        umount "$snap_dir" || fatal "Failed to unmount ${snap_dir}"
    fi

    if [[ -e "$snap_dir" ]]; then
        if path_is_btrfs_subvolume "$snap_dir"; then
            dir_is_empty "$snap_dir" || fatal "${snap_dir} is a populated subvolume. Cannot proceed safely."
            btrfs subvolume delete "$snap_dir" >/dev/null || true
        else
            dir_is_empty "$snap_dir" || fatal "${snap_dir} directory is not empty after unmounting."
            rmdir "$snap_dir" 2>/dev/null || true
        fi
    fi

    # CHROOT FIX: Inject --no-dbus
    snapper --no-dbus -c "$config_name" create-config "$config_path"
    ROLLBACK_CMDS+=("snapper --no-dbus -c ${config_name} delete-config")
    info "Created Snapper ${config_name} config."
}

ensure_top_level_snapshots_subvolume() {
    local base_path="$1" subvol_target="$2" tmp_mnt
    tmp_mnt="$(mount_top_level_for_base "$base_path")"

    if [[ -e "${tmp_mnt}/${subvol_target}" ]]; then
        path_is_btrfs_subvolume "${tmp_mnt}/${subvol_target}" || fatal "${subvol_target} exists but is not a subvolume."
        info "Top-level subvolume ${subvol_target} already exists."
    else
        btrfs subvolume create "${tmp_mnt}/${subvol_target}" >/dev/null
        info "Created top-level subvolume ${subvol_target}."
    fi

    release_temp_mount "$tmp_mnt"
}

migrate_regular_item_into_dir() {
    local src_item="$1" dst_dir="$2" base dst_item
    base="$(basename "$src_item")"
    dst_item="${dst_dir}/${base}"

    if path_exists "$dst_item"; then
        if test -f "$src_item" && test -f "$dst_item" && cmp -s "$src_item" "$dst_item"; then
            rm -f -- "$src_item"
            return 0
        fi
        fatal "Metadata conflict while migrating ${src_item}; destination ${dst_item} already exists."
    fi

    cp -a -- "$src_item" "$dst_dir/" || fatal "Failed to copy ${src_item} into ${dst_dir}."
    rm -rf --one-file-system -- "$src_item" || fatal "Failed to remove migrated source item ${src_item}."
}

migrate_single_legacy_snapshot_entry() {
    local src_entry="$1" dst_root="$2" entry_name="$3"
    local dst_entry="$dst_root/$entry_name"
    local item base

    mkdir -p -- "$dst_entry"

    while IFS= read -r -d '' item; do
        base="${item##*/}"

        if path_is_btrfs_subvolume "$item"; then
            if [[ "$base" != "snapshot" ]]; then
                fatal "Unexpected nested subvolume ${item} inside legacy Snapper entry ${src_entry}."
            fi

            if path_exists "${dst_entry}/snapshot"; then
                fatal "Destination snapshot subvolume ${dst_entry}/snapshot already exists."
            fi

            if btrfs_subvolume_is_ro "$item"; then
                btrfs subvolume snapshot -r "$item" "${dst_entry}/snapshot" >/dev/null || fatal "Failed to clone read-only snapshot ${item}."
            else
                btrfs subvolume snapshot "$item" "${dst_entry}/snapshot" >/dev/null || fatal "Failed to clone writable snapshot ${item}."
            fi

            btrfs subvolume delete "$item" >/dev/null || fatal "Failed to delete old snapshot subvolume ${item} after cloning."
        else
            migrate_regular_item_into_dir "$item" "$dst_entry"
        fi
    done < <(find "$src_entry" -mindepth 1 -maxdepth 1 -print0 2>/dev/null)

    dir_is_empty "$src_entry" || fatal "Legacy Snapper entry ${src_entry} is not empty after migration."
    rmdir "$src_entry" || fatal "Failed to remove drained legacy entry directory ${src_entry}."
}

migrate_existing_nested_snapshots() {
    local base_path="$1" mount_target="$2" subvol_target="$3"
    local tmp_mnt="" base_subvol="" src_path dst_path src_entry entry

    base_subvol="$(get_mount_subvolume_path "$base_path" || true)"
    tmp_mnt="$(mount_top_level_for_base "$base_path")"

    src_path="$tmp_mnt"
    [[ -n "$base_subvol" ]] && src_path+="/${base_subvol#/}"
    src_path+="/.snapshots"

    dst_path="${tmp_mnt}/${subvol_target#/}"

    if ! path_exists "$src_path"; then
        release_temp_mount "$tmp_mnt"
        return 0
    fi

    path_is_btrfs_subvolume "$src_path" || fatal "Legacy snapshots path behind ${mount_target} exists, but is not a Btrfs subvolume."
    path_is_btrfs_subvolume "$dst_path" || fatal "Target subvolume ${subvol_target} is missing or invalid."

    if dir_is_empty "$src_path"; then
        btrfs subvolume delete "$src_path" >/dev/null || fatal "Failed to delete empty legacy snapshots subvolume ${src_path}."
        info "Removed empty legacy snapshots subvolume behind ${mount_target}."
        release_temp_mount "$tmp_mnt"
        return 0
    fi

    info "Migrating existing Snapper data from legacy ${mount_target} into top-level ${subvol_target}..."

    while IFS= read -r -d '' entry; do
        [[ -n "$entry" ]] || continue
        src_entry="${src_path}/${entry}"

        test -d "$src_entry" || fatal "Unexpected non-directory item ${src_entry} under legacy snapshots root."
        migrate_single_legacy_snapshot_entry "$src_entry" "$dst_path" "$entry"
    done < <(find "$src_path" -mindepth 1 -maxdepth 1 -printf '%f\0' 2>/dev/null)

    dir_is_empty "$src_path" || fatal "Legacy snapshots root ${src_path} is not empty after migration."
    btrfs subvolume delete "$src_path" >/dev/null || fatal "Failed to delete drained legacy snapshots root ${src_path}."

    info "Migrated existing snapshots into ${subvol_target}."
    release_temp_mount "$tmp_mnt"
}

prepare_snapshots_mountpoint() {
    local base_path="$1" mount_target="$2" subvol_target="$3"

    [[ -L "$mount_target" ]] && fatal "Symlink detected at ${mount_target}."
    mkdir -p "$mount_target"

    if mountpoint -q "$mount_target"; then
        if current_snapshots_mount_matches_expected "$mount_target" "$subvol_target" "$base_path"; then
            chmod 750 "$mount_target"
            info "${mount_target} is already mounted from ${subvol_target}."
            return 0
        fi

        warn "${mount_target} is mounted from an unexpected source. Temporarily unmounting it to repair layout..."
        umount "$mount_target" || fatal "Failed to unmount ${mount_target}"
    fi

    migrate_existing_nested_snapshots "$base_path" "$mount_target" "$subvol_target"

    mkdir -p "$mount_target"

    if path_is_btrfs_subvolume "$mount_target"; then
        dir_is_empty "$mount_target" || fatal "Populated nested subvolume still present at ${mount_target} after migration."
        btrfs subvolume delete "$mount_target" >/dev/null || fatal "Failed to delete empty nested subvolume ${mount_target}."
        mkdir -p "$mount_target"
        info "Removed empty nested subvolume at ${mount_target}."
        return 0
    fi

    dir_is_empty "$mount_target" || fatal "Directory ${mount_target} is not empty."
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
        /^[[:space:]]*#/ || NF < 2 { print $0; next }
        {
            curr_mp = $2
            if (curr_mp != "/") sub(/\/+$/, "", curr_mp)

            if (curr_mp == mp) {
                if (!done) { print newline; done = 1 }
                next
            }
            print $0
        }
        END { if (!done) print newline }
    ' /etc/fstab > "$tmp"

    if ! findmnt --verify --tab-file "$tmp" >/dev/null 2>&1; then
        fatal "Generated fstab failed libmount validation."
    fi

    if test -f /etc/fstab && cmp -s "$tmp" /etc/fstab; then
        rm -f "$tmp"
        remove_array_value ACTIVE_TEMP_FILES "$tmp"
        info "/etc/fstab already contains the correct snapshot entries."
        return 0
    fi

    backup_file /etc/fstab
    atomic_write /etc/fstab "$tmp"
    rm -f "$tmp"
    remove_array_value ACTIVE_TEMP_FILES "$tmp"

    systemctl daemon-reload 2>/dev/null || true
    info "Ensured entry in /etc/fstab"
}

mount_snapshots() {
    local mount_target="$1" expected_subvol="$2" base_target="$3"
    mkdir -p "$mount_target"
    mountpoint -q "$mount_target" || mount "$mount_target"
    verify_snapshots_mount "$mount_target" "$expected_subvol" "$base_target"
    ROLLBACK_CMDS=()
}

verify_snapper_works() {
    # CHROOT FIX: Inject --no-dbus
    snapper --no-dbus -c "$1" list >/dev/null 2>&1 || fatal "Snapper $1 config is broken."
}

snapper_cleanup_counts() {
    # CHROOT FIX: Inject --no-dbus
    snapper --no-dbus --csv -c "$1" list 2>/dev/null | awk -F',' '
        NR == 1 {
            for (i = 1; i <= NF; i++) {
                if ($i == "number") num_col = i
                else if ($i == "cleanup") cleanup_col = i
            }
            next
        }
        num_col && cleanup_col &&
        $num_col ~ /^[0-9]+$/ &&
        $num_col != "0" {
            if ($cleanup_col == "number") number_count++
            else if ($cleanup_col == "important") important_count++
        }
        END {
            printf "%d %d\n", number_count + 0, important_count + 0
        }
    '
}

tune_snapper() {
    local cfg="$1"
    local default_limit=5 reserve=3
    local number_count=0 important_count=0
    local number_limit="$default_limit" important_limit="$default_limit"

    read -r number_count important_count < <(snapper_cleanup_counts "$cfg")

    if (( number_count > default_limit )); then
        number_limit=$(( number_count + reserve ))
    fi
    if (( important_count > default_limit )); then
        important_limit=$(( important_count + reserve ))
    fi

    # CHROOT FIX: Inject --no-dbus
    snapper --no-dbus -c "$cfg" set-config \
        TIMELINE_CREATE="no" \
        NUMBER_CLEANUP="yes" \
        NUMBER_LIMIT="${number_limit}" \
        NUMBER_LIMIT_IMPORTANT="${important_limit}" \
        SPACE_LIMIT="0.0" \
        FREE_LIMIT="0.0"
}

quiesce_snapper() {
    # Handled carefully since chroot environment doesn't run systemd init
    if systemctl is-active --quiet snapper-timeline.timer 2>/dev/null; then
        systemctl stop snapper-timeline.timer snapper-cleanup.timer 2>/dev/null || true
    fi
}

apply_global_btrfs_tuning() {
    btrfs quota disable / 2>/dev/null || true
    info "Applied global Btrfs tuning parameters."
}

write_tmpfiles_override() {
    local target="$1" content="$2" tmp
    tmp="$(mktemp)"
    ACTIVE_TEMP_FILES+=("$tmp")
    printf '%s\n' "$content" > "$tmp"

    if test -f "$target" && cmp -s "$tmp" "$target"; then
        rm -f "$tmp"
        remove_array_value ACTIVE_TEMP_FILES "$tmp"
        return 1
    fi

    backup_file "$target"
    atomic_write "$target" "$tmp"
    rm -f "$tmp"
    remove_array_value ACTIVE_TEMP_FILES "$tmp"
    return 0
}

enforce_flat_topology() {
    local sv changed=false

    for sv in /var/lib/machines /var/lib/portables; do
        if findmnt -M "$sv" >/dev/null 2>&1; then
            continue
        fi

        if path_is_btrfs_subvolume "$sv"; then
            btrfs subvolume delete "$sv" >/dev/null 2>&1 || warn "Failed to delete subvolume $sv"
            info "Deleted nested systemd subvolume: $sv"
        fi

        if [[ ! -e "$sv" ]]; then
            mkdir -p "$sv"
            chmod 0755 "$sv"
        fi
    done

    mkdir -p /etc/tmpfiles.d

    if write_tmpfiles_override /etc/tmpfiles.d/systemd-nspawn.conf "d /var/lib/machines 0755 - - -"; then
        changed=true
    fi

    if write_tmpfiles_override /etc/tmpfiles.d/portables.conf "d /var/lib/portables 0755 - - -"; then
        changed=true
    fi

    if [[ "$changed" == true ]]; then
        info "Applied systemd tmpfiles overrides."
    fi
}

preflight_checks() {
    require_cmd pacman
    require_cmd findmnt
    require_cmd find
    require_cmd awk
    require_cmd sed
    require_cmd grep
    require_cmd cmp
    require_cmd realpath
    require_cmd stat
    require_cmd mktemp
    require_cmd mountpoint
    require_cmd btrfs
}

preflight_checks
quiesce_snapper
execute "Reinstall Snapper runtime packages" install_packages
post_install_checks

# --- ROOT SNAPSHOT CONFIG ---
execute "Create Snapper root" ensure_snapper_config "root" "/"
execute "Create top-level @snapshots" ensure_top_level_snapshots_subvolume "/" "@snapshots"
execute "Prepare /.snapshots" prepare_snapshots_mountpoint "/" "/.snapshots" "@snapshots"
execute "Write /.snapshots to fstab" ensure_fstab_entry_for_snapshots "/" "/.snapshots" "@snapshots"
execute "Mount /.snapshots" mount_snapshots "/.snapshots" "@snapshots" "/"
execute "Verify Snapper root" verify_snapper_works "root"
execute "Tune Snapper root" tune_snapper "root"

# --- HOME SNAPSHOT CONFIG ---
execute "Create Snapper home" ensure_snapper_config "home" "/home"
execute "Create top-level @home_snapshots" ensure_top_level_snapshots_subvolume "/home" "@home_snapshots"
execute "Prepare /home/.snapshots" prepare_snapshots_mountpoint "/home" "/home/.snapshots" "@home_snapshots"
execute "Write /home/.snapshots to fstab" ensure_fstab_entry_for_snapshots "/home" "/home/.snapshots" "@home_snapshots"
execute "Mount /home/.snapshots" mount_snapshots "/home/.snapshots" "@home_snapshots" "/home"
execute "Verify Snapper home" verify_snapper_works "home"
execute "Tune Snapper home" tune_snapper "home"

execute "Apply Global Btrfs Settings" apply_global_btrfs_tuning
execute "Enforce Flat Topology" enforce_flat_topology
