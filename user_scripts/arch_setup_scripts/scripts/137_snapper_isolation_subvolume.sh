#!/usr/bin/env bash
#d: Set up isolated Snapper snapshots for root and home

set -Eeuo pipefail
export LC_ALL=C

# --- USER CONFIGURATION ---
# Set the exact time of day to take the daily snapshot using 24-hour format.
# Example: "20:00" is 8:00 PM.
SNAPSHOT_TIME="20:00"

# Set the strict limit on how many automated snapshots to keep per configuration
SNAPSHOT_RETENTION_LIMIT=6
# --------------------------

AUTO_MODE=false
[[ "${1:-}" == "--auto" ]] && AUTO_MODE=true

declare -A BACKED_UP=()
declare -A CACHE_MNT_SOURCE=()
declare -A CACHE_MNT_UUID=()
declare -A CACHE_MNT_OPTS=()

declare -a ACTIVE_TEMP_MOUNTS=()
declare -a ACTIVE_TEMP_FILES=()
SUDO_PID=""
DUSKY_SNAPSHOT_WAS_ACTIVE=false
SNAPPER_CLEANUP_WAS_ACTIVE=false
SNAPPER_TIMELINE_WAS_ACTIVE=false
SNAPPER_TIMELINE_WAS_ENABLED=false
SETUP_COMPLETE=false

cleanup() {
    local mnt f

    if (( ${#ACTIVE_TEMP_MOUNTS[@]} > 0 )); then
        for mnt in "${ACTIVE_TEMP_MOUNTS[@]}"; do
            [[ -n "$mnt" ]] || continue
            if mountpoint -q "$mnt"; then
                sudo umount "$mnt" 2>/dev/null || true
            fi
            rmdir "$mnt" 2>/dev/null || true
        done
    fi

    if (( ${#ACTIVE_TEMP_FILES[@]} > 0 )); then
        for f in "${ACTIVE_TEMP_FILES[@]}"; do
            [[ -n "$f" && -f "$f" ]] && sudo rm -f "$f" 2>/dev/null || true
        done
    fi

    if [[ "$SNAPPER_CLEANUP_WAS_ACTIVE" == true ]] && ! systemctl is-active --quiet snapper-cleanup.timer; then
        sudo systemctl start snapper-cleanup.timer || warn "Could not restart the Snapper cleanup timer."
    fi

    if [[ "$SETUP_COMPLETE" != true ]]; then
        if [[ "$DUSKY_SNAPSHOT_WAS_ACTIVE" == true ]] && ! systemctl is-active --quiet dusky_snapshot.timer; then
            sudo systemctl start dusky_snapshot.timer || warn "Could not restart the scheduled snapshot timer."
        fi
        if [[ "$SNAPPER_TIMELINE_WAS_ENABLED" == true ]] && ! systemctl is-enabled --quiet snapper-timeline.timer; then
            sudo systemctl enable snapper-timeline.timer || warn "Could not restore the Snapper timeline timer enablement."
        fi
        if [[ "$SNAPPER_TIMELINE_WAS_ACTIVE" == true ]] && ! systemctl is-active --quiet snapper-timeline.timer; then
            sudo systemctl start snapper-timeline.timer || warn "Could not restart the Snapper timeline timer."
        fi
    fi

    kill "${SUDO_PID:-}" 2>/dev/null || true
}

trap_exit() { cleanup; }

trap_interrupt() {
    printf '\n\033[1;31m[FATAL]\033[0m Script interrupted.\n' >&2
    exit 130
}

trap_error() {
    local line_no="$1" cmd="$2"
    printf '\n\033[1;33m[WARN]\033[0m Script encountered an error at line %d (command: %s). Exiting gracefully.\n' "$line_no" "$cmd" >&2
    exit 0
}
trap 'trap_error "$LINENO" "$BASH_COMMAND"' ERR
trap trap_exit EXIT
trap trap_interrupt INT TERM HUP

fatal() {
    printf '\033[1;33m[WARN]\033[0m %s (exiting gracefully)\n' "$1" >&2
    exit 0
}

info() {
    printf '\033[1;32m[INFO]\033[0m %s\n' "$1"
}

warn() {
    printf '\033[1;33m[WARN]\033[0m %s\n' "$1" >&2
}

execute() {
    local desc="$1"; shift

    if [[ "$AUTO_MODE" == true ]]; then
        "$@"
        return 0
    fi

    printf '\n\033[1;34m[ACTION]\033[0m %s\n' "$desc"
    read -r -p "Execute this step? [Y/n] " response || fatal "Input closed; aborting."

    if [[ "${response,,}" =~ ^(n|no)$ ]]; then
        fatal "Setup stopped before '${desc}'; later steps depend on this one."
    fi

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

remove_array_value() {
    local array_name="$1" value="$2" item
    local -n arr_ref="$array_name"
    local -a new_arr=()

    if (( ${#arr_ref[@]} > 0 )); then
        for item in "${arr_ref[@]}"; do
            [[ -n "$item" && "$item" != "$value" ]] && new_arr+=("$item")
        done
    fi

    if (( ${#new_arr[@]} > 0 )); then
        arr_ref=("${new_arr[@]}")
    else
        arr_ref=()
    fi
}

sudo_path_exists() { sudo test -e "$1"; }

atomic_write() {
    local target="$1" src="$2" target_dir tmp_target

    target_dir="$(dirname "$target")"
    tmp_target="$(sudo mktemp "${target_dir}/.tmp.XXXXXX")"
    ACTIVE_TEMP_FILES+=("$tmp_target")

    sudo cp "$src" "$tmp_target"
    sudo chmod 0644 "$tmp_target"
    sudo mv "$tmp_target" "$target"

    remove_array_value ACTIVE_TEMP_FILES "$tmp_target"
    sudo sync -f "$target_dir" || fatal "Could not sync filesystem containing $target_dir"
}

load_mount_info() {
    local target="$1"

    [[ -v CACHE_MNT_SOURCE["$target"] ]] && return 0

    local findmnt_out source uuid opts

    findmnt_out="$(sudo findmnt -n -e -o SOURCE,UUID,OPTIONS -M "$target" 2>/dev/null || true)"
    [[ -n "$findmnt_out" ]] || fatal "Could not determine mount info for $target"

    read -r source uuid opts <<< "$findmnt_out"
    source="${source%%\[*}"
    printf -v source '%b' "$source"

    if [[ -z "$uuid" || "$uuid" == "-" ]]; then
        uuid="$(sudo blkid -s UUID -o value "$source" 2>/dev/null || true)"
    fi

    [[ -n "$uuid" ]] || fatal "Could not determine UUID for $target"

    CACHE_MNT_SOURCE["$target"]="$source"
    CACHE_MNT_UUID["$target"]="$uuid"
    CACHE_MNT_OPTS["$target"]="$opts"
}

get_mount_path() {
    local field="$1" value
    shift
    value="$(sudo findmnt -rn -o "$field" "$@")" || return 1
    # --raw hex-escapes unsafe bytes, including spaces and literal backslashes.
    printf '%b\n' "$value"
}

get_mount_subvolume_path() {
    local target="$1" path
    path="$(get_mount_path FSROOT -M "$target")" || fatal "Could not identify mounted Btrfs root for $target"
    path="${path#/}"
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
    sudo test -d "$1" || return 0

    local entries
    entries="$(sudo find "$1" -mindepth 1 -maxdepth 1 -print -quit)" || fatal "Could not inspect $1"
    [[ -z "$entries" ]]
}

path_is_btrfs_subvolume() {
    sudo btrfs subvolume show "$1" >/dev/null 2>&1
}

delete_unmounted_subvolume() {
    local target="$1" subvol_id fs_uuid default_id mounts_json mount_list mounted mounted_id
    subvol_id="$(sudo btrfs inspect-internal rootid "$target")" || fatal "Cannot identify subvolume $target"
    fs_uuid="$(sudo findmnt -n -e -o UUID -T "$target")" || fatal "Cannot identify filesystem for $target"
    default_id="$(sudo btrfs subvolume get-default "$target" | awk '{print $2}')" || fatal "Cannot inspect Btrfs default for $target"
    [[ "$subvol_id" != "$default_id" ]] || fatal "Refusing to delete default subvolume $target"
    mounts_json="$(sudo findmnt --json --list -t btrfs -o TARGET,UUID)" || fatal "Cannot inspect Btrfs mounts"
    mount_list="$(mktemp)"
    ACTIVE_TEMP_FILES+=("$mount_list")
    python3 -c 'import json, sys
uuid = sys.argv[1]
for item in json.load(sys.stdin)["filesystems"]:
    if item.get("uuid") == uuid:
        sys.stdout.buffer.write(item["target"].encode() + b"\0")' "$fs_uuid" <<< "$mounts_json" > "$mount_list" || fatal "Cannot parse Btrfs mounts"
    while IFS= read -r -d '' mounted; do
        mounted_id="$(sudo btrfs inspect-internal rootid "$mounted")" || fatal "Cannot inspect mounted subvolume $mounted"
        [[ "$mounted_id" != "$subvol_id" ]] || fatal "Refusing to delete mounted subvolume $target (mounted at $mounted)"
    done < "$mount_list"
    rm -f "$mount_list"
    remove_array_value ACTIVE_TEMP_FILES "$mount_list"
    sudo btrfs subvolume delete --commit-after "$target" >/dev/null
}

btrfs_subvolume_is_ro() {
    local out
    out="$(sudo btrfs property get -t subvol "$1" ro 2>/dev/null || true)"

    if [[ "$out" == *"ro=true"* ]]; then
        return 0
    fi

    return 1
}

mount_top_level_for_base() {
    local base_path="$1" result_var="$2" root_source root_opts new_mount extra_opts="subvolid=5"

    load_mount_info "$base_path"

    root_source="${CACHE_MNT_SOURCE["$base_path"]}"
    root_opts="${CACHE_MNT_OPTS["$base_path"]}"

    [[ ",$root_opts," == *",degraded,"* ]] && extra_opts+=",degraded"

    new_mount="$(mktemp -d)"
    ACTIVE_TEMP_MOUNTS+=("$new_mount")
    sudo mount -o "$extra_opts" "$root_source" "$new_mount" || fatal "Mount failed."
    printf -v "$result_var" '%s' "$new_mount"
}

release_temp_mount() {
    local tmp_mnt="$1"

    [[ -n "$tmp_mnt" ]] || return 0

    if mountpoint -q "$tmp_mnt"; then
        sudo umount "$tmp_mnt" || fatal "Could not unmount temporary Btrfs mount $tmp_mnt"
    fi

    rmdir "$tmp_mnt" || fatal "Could not remove temporary mount directory $tmp_mnt"
    remove_array_value ACTIVE_TEMP_MOUNTS "$tmp_mnt"
}

current_snapshots_mount_matches_expected() {
    local mount_target="$1" expected_subvol="$2" base_target="$3" target_uuid
    local snap_uuid mounted_root

    load_mount_info "$base_target"
    target_uuid="${CACHE_MNT_UUID["$base_target"]}"

    sudo findmnt -M "$mount_target" >/dev/null 2>&1 || return 1

    snap_uuid="$(sudo findmnt -n -e -o UUID -M "$mount_target")" || return 1
    mounted_root="$(get_mount_path FSROOT -M "$mount_target")" || return 1

    [[ "$snap_uuid" == "$target_uuid" ]] || return 1

    [[ "$mounted_root" == "/${expected_subvol#/}" ]]
}

verify_snapshots_mount() {
    local mount_target="$1" expected_subvol="$2" base_target="$3" target_uuid
    local snap_uuid mounted_root

    load_mount_info "$base_target"
    target_uuid="${CACHE_MNT_UUID["$base_target"]}"

    findmnt -M "$mount_target" >/dev/null 2>&1 || fatal "${mount_target} is not mounted."

    snap_uuid="$(sudo findmnt -n -e -o UUID -M "$mount_target")" || return 1
    mounted_root="$(get_mount_path FSROOT -M "$mount_target")" || return 1

    [[ "$snap_uuid" == "$target_uuid" ]] || fatal "${mount_target} filesystem UUID mismatch."

    [[ "$mounted_root" == "/${expected_subvol#/}" ]] || fatal "${mount_target} subvol mismatch."

    sudo chmod 750 "$mount_target"
    info "${mount_target} is mounted correctly."
}

install_packages() {
    if ! pacman -Q snapper boost-libs btrfs-progs >/dev/null 2>&1; then
        warn "Required packages (snapper, boost-libs, btrfs-progs) are not installed. Skipping Snapper isolation setup."
        exit 0
    fi
    info "Snapper runtime packages are installed."
}

verify_snapper_runtime() {
    if ! sudo snapper --help >/dev/null 2>&1; then
        warn "snapper is installed but not runnable (e.g. library mismatch). Skipping Snapper isolation setup."
        exit 0
    fi
}

post_install_checks() {
    require_cmd btrfs
    require_cmd python3
    require_cmd snapper
    require_cmd systemctl

    verify_snapper_runtime

    if [[ -z "$(get_mount_subvolume_path / 2>/dev/null)" ]]; then
        warn "/ must be mounted from a Btrfs subvolume. Skipping Snapper isolation setup."
        exit 0
    fi
    if ! path_is_btrfs_subvolume "/home" 2>/dev/null || [[ -z "$(get_mount_subvolume_path /home 2>/dev/null)" ]]; then
        warn "/home must be mounted from a Btrfs subvolume. Skipping Snapper isolation setup."
        exit 0
    fi
}

ensure_snapper_config() {
    local config_name="$1" config_path="$2"
    local snap_dir="${config_path}/.snapshots"
    snap_dir="${snap_dir//\/\//\/}"

    if sudo snapper -c "$config_name" get-config >/dev/null 2>&1; then
        sudo grep -qxF "SUBVOLUME=\"${config_path}\"" "/etc/snapper/configs/${config_name}" ||
            fatal "Snapper ${config_name} points somewhere other than ${config_path}."
        info "Snapper ${config_name} exists."
        return 0
    fi

    if sudo test -f "/etc/snapper/configs/${config_name}"; then
        fatal "Snapper config '${config_name}' exists but cannot be loaded; inspect it before retrying."
    fi

    if sudo test -d /etc/snapper/configs; then
        local conf conflicting_name

        while read -r -d '' conf; do
            [[ -n "$conf" ]] || continue

            if sudo grep -qxF "SUBVOLUME=\"${config_path}\"" "$conf" 2>/dev/null; then
                conflicting_name="$(basename "$conf")"
                fatal "Subvolume ${config_path} is already covered by Snapper config '${conflicting_name}'."
            fi
        done < <(sudo find /etc/snapper/configs/ -mindepth 1 -maxdepth 1 -type f -print0 2>/dev/null || true)
    fi

    if mountpoint -q "$snap_dir"; then
        warn "${snap_dir} is already mounted. Temporarily unmounting to allow Snapper to initialize..."
        sudo umount "$snap_dir" || fatal "Failed to unmount ${snap_dir}"
    fi

    if sudo_path_exists "$snap_dir"; then
        if path_is_btrfs_subvolume "$snap_dir"; then
            dir_is_empty "$snap_dir" || fatal "${snap_dir} is a populated subvolume. Cannot proceed safely."
            delete_unmounted_subvolume "$snap_dir" || fatal "Could not remove empty ${snap_dir}."
        else
            dir_is_empty "$snap_dir" || fatal "${snap_dir} directory is not empty after unmounting."
            sudo rmdir "$snap_dir" || fatal "Could not remove empty ${snap_dir}."
        fi
    fi

    sudo snapper -c "$config_name" create-config "$config_path"

    info "Created Snapper ${config_name} config."
}

ensure_top_level_snapshots_subvolume() {
    local base_path="$1" subvol_target="$2" tmp_mnt

    mount_top_level_for_base "$base_path" tmp_mnt

    if sudo_path_exists "${tmp_mnt}/${subvol_target}"; then
        path_is_btrfs_subvolume "${tmp_mnt}/${subvol_target}" || fatal "${subvol_target} exists but is not a subvolume."
        info "Top-level subvolume ${subvol_target} already exists."
    else
        sudo btrfs subvolume create "${tmp_mnt}/${subvol_target}" >/dev/null
        info "Created top-level subvolume ${subvol_target}."
    fi

    release_temp_mount "$tmp_mnt"
}

migrate_regular_item_into_dir() {
    local src_item="$1" dst_dir="$2" base dst_item

    sudo test ! -d "$src_item" || fatal "Unexpected directory under Snapper metadata: $src_item"

    base="$(basename "$src_item")"
    dst_item="${dst_dir}/${base}"

    if sudo_path_exists "$dst_item" || sudo test -L "$dst_item"; then
        if sudo test -f "$src_item" && sudo test -f "$dst_item" && sudo cmp -s "$src_item" "$dst_item"; then
            sudo rm -f -- "$src_item"
            return 0
        fi

        fatal "Metadata conflict while migrating ${src_item}; destination ${dst_item} already exists."
    fi

    sudo cp -a -- "$src_item" "$dst_dir/" || fatal "Failed to copy ${src_item} into ${dst_dir}."
    sudo rm -f -- "$src_item" || fatal "Failed to remove migrated source item ${src_item}."
}

migrate_single_legacy_snapshot_entry() {
    local src_entry="$1" dst_root="$2" entry_name="$3"
    local dst_entry="$dst_root/$entry_name"
    local item base

    sudo mkdir -p -- "$dst_entry"

    while IFS= read -r -d '' item; do
        base="${item##*/}"

        if path_is_btrfs_subvolume "$item"; then
            if [[ "$base" != "snapshot" ]]; then
                fatal "Unexpected nested subvolume ${item} inside legacy Snapper entry ${src_entry}."
            fi

            if sudo_path_exists "${dst_entry}/snapshot"; then
                fatal "Destination snapshot subvolume ${dst_entry}/snapshot already exists. Manual conflict resolution required."
            fi

            if btrfs_subvolume_is_ro "$item"; then
                sudo btrfs subvolume snapshot -r "$item" "${dst_entry}/snapshot" >/dev/null || fatal "Failed to clone read-only snapshot ${item} to ${dst_entry}/snapshot."
            else
                sudo btrfs subvolume snapshot "$item" "${dst_entry}/snapshot" >/dev/null || fatal "Failed to clone writable snapshot ${item} to ${dst_entry}/snapshot."
            fi

            delete_unmounted_subvolume "$item" || fatal "Failed to delete old snapshot subvolume ${item} after cloning."
        else
            migrate_regular_item_into_dir "$item" "$dst_entry"
        fi
    done < <(sudo find "$src_entry" -mindepth 1 -maxdepth 1 -print0 2>/dev/null)

    dir_is_empty "$src_entry" || fatal "Legacy Snapper entry ${src_entry} is not empty after migration."
    sudo rmdir "$src_entry" || fatal "Failed to remove drained legacy entry directory ${src_entry}."
}

migrate_existing_nested_snapshots() {
    local base_path="$1" mount_target="$2" subvol_target="$3"
    local tmp_mnt="" base_subvol="" src_path dst_path src_entry entry

    base_subvol="$(get_mount_subvolume_path "$base_path")"
    mount_top_level_for_base "$base_path" tmp_mnt

    src_path="$tmp_mnt"
    [[ -n "$base_subvol" ]] && src_path+="/${base_subvol#/}"
    src_path+="/.snapshots"

    dst_path="${tmp_mnt}/${subvol_target#/}"

    if ! sudo_path_exists "$src_path"; then
        release_temp_mount "$tmp_mnt"
        return 0
    fi

    path_is_btrfs_subvolume "$dst_path" || fatal "Target subvolume ${subvol_target} is missing or invalid."

    if path_is_btrfs_subvolume "$src_path"; then
        if dir_is_empty "$src_path"; then
            delete_unmounted_subvolume "$src_path" || fatal "Failed to delete empty legacy snapshots subvolume ${src_path}."
            info "Removed empty legacy snapshots subvolume behind ${mount_target}."
            release_temp_mount "$tmp_mnt"
            return 0
        fi

        info "Migrating existing Snapper data from legacy subvolume ${mount_target} into top-level ${subvol_target}..."

        while IFS= read -r -d '' entry; do
            [[ -n "$entry" ]] || continue
            src_entry="${src_path}/${entry}"

            sudo test -d "$src_entry" || fatal "Unexpected non-directory item ${src_entry} under legacy snapshots root."
            migrate_single_legacy_snapshot_entry "$src_entry" "$dst_path" "$entry"
        done < <(sudo find "$src_path" -mindepth 1 -maxdepth 1 -printf '%f\0' 2>/dev/null)

        dir_is_empty "$src_path" || fatal "Legacy snapshots root ${src_path} is not empty after migration."
        delete_unmounted_subvolume "$src_path" || fatal "Failed to delete drained legacy snapshots root ${src_path}."

        info "Migrated existing snapshots into ${subvol_target}."
    else
        if dir_is_empty "$src_path"; then
            release_temp_mount "$tmp_mnt"
            return 0
        fi

        info "Migrating existing Snapper data from legacy directory ${mount_target} into top-level ${subvol_target}..."

        while IFS= read -r -d '' entry; do
            [[ -n "$entry" ]] || continue
            src_entry="${src_path}/${entry}"

            sudo test -d "$src_entry" || fatal "Unexpected non-directory item ${src_entry} under legacy snapshots root."
            migrate_single_legacy_snapshot_entry "$src_entry" "$dst_path" "$entry"
        done < <(sudo find "$src_path" -mindepth 1 -maxdepth 1 -printf '%f\0' 2>/dev/null)

        dir_is_empty "$src_path" || fatal "Legacy snapshots root ${src_path} is not empty after migration."
        sudo rmdir "$src_path" || fatal "Failed to remove drained legacy snapshots directory ${src_path}."

        info "Migrated existing snapshots into ${subvol_target}."
    fi

    release_temp_mount "$tmp_mnt"
}

legacy_snapshots_need_migration() {
    local base_path="$1" tmp_mnt="" base_subvol hidden needed=1
    base_subvol="$(get_mount_subvolume_path "$base_path")"
    mount_top_level_for_base "$base_path" tmp_mnt
    hidden="$tmp_mnt"
    [[ -n "$base_subvol" ]] && hidden+="/${base_subvol#/}"
    hidden+="/.snapshots"
    if sudo_path_exists "$hidden" && { path_is_btrfs_subvolume "$hidden" || ! dir_is_empty "$hidden"; }; then
        needed=0
    fi
    release_temp_mount "$tmp_mnt"
    return "$needed"
}

prepare_snapshots_mountpoint() {
    local base_path="$1" mount_target="$2" subvol_target="$3"

    [[ -L "$mount_target" ]] && fatal "Symlink detected at ${mount_target}."

    sudo mkdir -p "$mount_target"

    if mountpoint -q "$mount_target"; then
        if current_snapshots_mount_matches_expected "$mount_target" "$subvol_target" "$base_path"; then
            if ! legacy_snapshots_need_migration "$base_path"; then
                sudo chmod 750 "$mount_target"
                info "${mount_target} is already mounted from ${subvol_target}."
                return 0
            fi
            info "Temporarily unmounting ${mount_target} to migrate hidden legacy snapshots."
        else
            warn "${mount_target} is mounted from an unexpected source. Temporarily unmounting it to repair layout..."
        fi
        sudo umount "$mount_target" || fatal "Failed to unmount ${mount_target}"
    fi

    migrate_existing_nested_snapshots "$base_path" "$mount_target" "$subvol_target"

    sudo mkdir -p "$mount_target"

    if path_is_btrfs_subvolume "$mount_target"; then
        dir_is_empty "$mount_target" || fatal "Populated nested subvolume still present at ${mount_target} after migration."
        delete_unmounted_subvolume "$mount_target" || fatal "Failed to delete empty nested subvolume ${mount_target}."
        sudo mkdir -p "$mount_target"
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

    canonical_target="$(sudo realpath -m "$mount_target")"
    # fstab uses octal escapes for whitespace and literal backslashes.
    canonical_target="${canonical_target//\\/\\134}"
    canonical_target="${canonical_target// /\\040}"
    canonical_target="${canonical_target//$'\t'/\\011}"
    canonical_target="${canonical_target//$'\n'/\\012}"
    newline="UUID=${fs_uuid} ${canonical_target} btrfs ${mount_opts} 0 0"

    tmp="$(mktemp)"
    ACTIVE_TEMP_FILES+=("$tmp")

    DUSKY_MOUNT_TARGET="$canonical_target" DUSKY_FSTAB_ENTRY="$newline" awk '
        BEGIN { done = 0; mp = ENVIRON["DUSKY_MOUNT_TARGET"]; newline = ENVIRON["DUSKY_FSTAB_ENTRY"] }

        /^[[:space:]]*#/ || NF < 2 {
            print $0
            next
        }

        {
            curr_mp = $2
            if (curr_mp != "/") sub(/\/+$/, "", curr_mp)

            # libmount/systemd require a parent before its child mounts.
            if (!done && index(curr_mp, mp "/") == 1) {
                print newline
                done = 1
            }
            if (curr_mp == mp) {
                if (!done) {
                    print newline
                    done = 1
                }
                next
            }

            print $0
        }

        END {
            if (!done) print newline
        }
    ' /etc/fstab > "$tmp"

    if ! sudo findmnt --verify --tab-file "$tmp" >/dev/null 2>&1; then
        fatal "Generated fstab failed libmount validation."
    fi

    if sudo test -f /etc/fstab && sudo cmp -s "$tmp" /etc/fstab; then
        rm -f "$tmp"
        remove_array_value ACTIVE_TEMP_FILES "$tmp"
        info "/etc/fstab already contains the correct snapshot entries."
        return 0
    fi

    backup_file /etc/fstab
    atomic_write /etc/fstab "$tmp"

    rm -f "$tmp"
    remove_array_value ACTIVE_TEMP_FILES "$tmp"

    sudo systemctl daemon-reload
    info "Ensured entry in /etc/fstab"
}

mount_snapshots() {
    local mount_target="$1" expected_subvol="$2" base_target="$3"

    sudo mkdir -p "$mount_target"
    mountpoint -q "$mount_target" || sudo mount "$mount_target"

    verify_snapshots_mount "$mount_target" "$expected_subvol" "$base_target"

}

verify_snapper_works() {
    sudo snapper -c "$1" list >/dev/null 2>&1 || fatal "Snapper $1 config is broken."
}

tune_snapper() {
    local cfg="$1"
    local strict_limit="${SNAPSHOT_RETENTION_LIMIT}"

    info "Configuring snapshot limits and disabling background comparisons for ${cfg}..."

    sudo snapper -c "$cfg" set-config \
        TIMELINE_CREATE="no" \
        NUMBER_CLEANUP="yes" \
        NUMBER_LIMIT="${strict_limit}" \
        NUMBER_LIMIT_IMPORTANT="${strict_limit}" \
        SPACE_LIMIT="0.0" \
        FREE_LIMIT="0.0" \
        BACKGROUND_COMPARISON="no" \
        QGROUP=""
}

quiesce_snapper() {
    if systemctl is-active --quiet dusky_snapshot.timer; then
        DUSKY_SNAPSHOT_WAS_ACTIVE=true
        sudo systemctl stop dusky_snapshot.timer || fatal "Could not pause the scheduled snapshot timer."
    fi
    if systemctl is-active --quiet snapper-cleanup.timer; then
        SNAPPER_CLEANUP_WAS_ACTIVE=true
    fi
    if systemctl is-active --quiet snapper-timeline.timer; then
        SNAPPER_TIMELINE_WAS_ACTIVE=true
    fi
    if systemctl is-enabled --quiet snapper-timeline.timer; then
        SNAPPER_TIMELINE_WAS_ENABLED=true
    fi
    if systemctl is-active --quiet snapper-timeline.timer || systemctl is-active --quiet snapper-cleanup.timer; then
        sudo systemctl stop snapper-timeline.timer snapper-cleanup.timer || fatal "Could not pause Snapper timers."
    fi
    # Let already-running jobs finish before moving snapshot or browser data.
    local service state
    for service in dusky_snapshot.service snapper-timeline.service snapper-cleanup.service; do
        while :; do
            state="$(systemctl show --property=ActiveState --value "$service")"
            case "$state" in
                activating|active|deactivating) sleep 0.2 ;;
                *) break ;;
            esac
        done
    done
}

# Keep browser state outside root/home snapshots, including after a home rollback.
# Local desktop accounts come from passwd; no installation username is embedded.
drain_browser_original() {
    local hidden="$1"
    sudo test -d "$hidden" || return 0
    # Keep the original directory/subvolume inode: deleting an underlying subvolume
    # would detach its overlying browser mount from pathname lookup.
    sudo find "$hidden" -mindepth 1 -maxdepth 1 -exec rm -rf --one-file-system -- {} +
}

browser_is_running() {
    # Linux comm is limited to 15 characters (chromium-browser is truncated).
    pgrep -u "$1" -x 'firefox|firefox-bin|chrome|chromium|chromium-browse|chromium-browser|google-chrome|chrome-headless' >/dev/null
}

isolate_browser_directory() {
    local base="$1" top="$2" uid="$3" gid="$4" path="$5" role="$6"
    # Check before inspecting paths or changing even an existing mount.
    if browser_is_running "$uid"; then
        warn "Browser isolation deferred for $path: close Firefox, Chromium and Chrome, then rerun this script. Existing browser data and mounts are unchanged."
        return 0
    fi
    local parent subvol destination pending hidden fsroot relative
    parent="$(dirname "$path")"
    if ! sudo test -d "$parent"; then
        sudo install -d -m 0700 -o "$uid" -g "$gid" -- "$parent"
    fi
    path="$(sudo realpath -m "$path")"
    parent="$(dirname "$path")"
    # A symlinked profile may resolve outside the account's home mount.
    local actual_base
    actual_base="$(get_mount_path TARGET -T "$parent")"
    load_mount_info "$actual_base"
    if [[ "${CACHE_MNT_UUID["$actual_base"]}" != "${CACHE_MNT_UUID["$base"]}" ]]; then
        warn "Browser directory $path is on a different filesystem from $base; skipping isolation."
        return 0
    fi
    base="$actual_base"
    subvol="@browser_${uid}_${role}"
    destination="$top/$subvol"
    pending="${destination}.pending"

    fsroot="$(get_mount_subvolume_path "$base")"
    relative="${path#"${base%/}"/}"
    hidden="$top/${fsroot:+$fsroot/}$relative"
    if sudo mountpoint -q "$path"; then
        if current_snapshots_mount_matches_expected "$path" "$subvol" "$base"; then
            ensure_fstab_entry_for_snapshots "$base" "$path" "$subvol"
            drain_browser_original "$hidden"
        else
            # Keep an existing top-level browser subvolume and its mount policy.
            # Require a matching fstab entry so isolation survives reboot/rollback.
            local mounted_root mounted_source entries
            mounted_root="$(get_mount_path FSROOT -M "$path")"
            mounted_source="$(sudo findmnt -n -e -o SOURCE -M "$path")"
            mounted_source="${mounted_source%%\[*}"
            if [[ "$mounted_root" != /* || "$mounted_root" == / || "${mounted_root#/}" == */* ]]; then
                warn "Browser directory $path is not mounted from a top-level subvolume; skipping isolation."
                return 0
            fi
            if [[ "$(sudo findmnt -n -e -o UUID -M "$path")" != "${CACHE_MNT_UUID["$base"]}" ]]; then
                warn "Browser directory $path is mounted from a different filesystem; skipping isolation."
                return 0
            fi
            if ! path_is_btrfs_subvolume "$path"; then
                warn "Browser mount $path is not a Btrfs subvolume; skipping isolation."
                return 0
            fi
            if ! entries="$(sudo findmnt --fstab --evaluate --json --list -M "$path" -o SOURCE,FSTYPE,OPTIONS 2>/dev/null)"; then
                warn "Existing browser mount $path needs a persistent fstab entry; skipping isolation."
                return 0
            fi
            if ! python3 -c 'import json, sys
source, root = sys.argv[1:]
rows = json.load(sys.stdin)["filesystems"]
assert len(rows) == 1
row = rows[0]
options = dict(item.split("=", 1) if "=" in item else (item, "")
               for item in row["options"].split(","))
assert row["source"] == source and row["fstype"] == "btrfs"
assert options.get("subvol", "").lstrip("/") == root.lstrip("/")
assert "noauto" not in options' "$mounted_source" "$mounted_root" <<< "$entries" 2>/dev/null; then
                warn "Existing browser mount $path does not match its persistent fstab entry; skipping isolation."
                return 0
            fi
            info "Keeping existing isolated browser mount: $path ($mounted_root)."
        fi
        return 0
    fi
    if sudo test -e "$path" && ! sudo test -d "$path"; then
        warn "Browser path is not a directory: $path; skipping isolation."
        return 0
    fi
    if [[ "$(sudo findmnt -n -e -o UUID -T "$parent")" != "${CACHE_MNT_UUID["$base"]}" ]]; then
        warn "Browser directory $path is on a different filesystem from $base; skipping isolation."
        return 0
    fi
    # The unmounted directory remains intact underneath the new mount until success.
    if sudo test -d "$path"; then
        local descendants entry prefix="${fsroot:+$fsroot/}$relative/"
        descendants="$(sudo btrfs subvolume list -o "$path" 2>/dev/null || true)"
        while IFS= read -r entry; do
            if [[ -n "$entry" && "${entry#* path }" == "$prefix"* ]]; then
                warn "Browser directory $path contains nested subvolumes; migrate those explicitly first. Skipping isolation."
                return 0
            fi
        done <<< "$descendants"
    fi
    if sudo test -e "$destination"; then
        if ! path_is_btrfs_subvolume "$destination"; then
            warn "$destination is not a subvolume; skipping isolation."
            return 0
        fi
    fi
    if ! sudo test -e "$destination" || ! dir_is_empty "$path"; then
        if sudo test -e "$pending"; then
            if ! path_is_btrfs_subvolume "$pending"; then
                warn "$pending is not a subvolume; skipping isolation."
                return 0
            fi
            delete_unmounted_subvolume "$pending"
        fi
        sudo btrfs subvolume create "$pending" >/dev/null
        if sudo test -d "$path"; then
            sudo cp -aT --reflink=auto -- "$path" "$pending"
        else
            sudo chown "$uid:$gid" "$pending"
            sudo chmod 0700 "$pending"
        fi
        sudo sync -f "$pending"
        if sudo test -e "$destination"; then
            delete_unmounted_subvolume "$destination"
        fi
        sudo mv -- "$pending" "$destination"
        sudo sync -f "$top"
    fi
    if ! sudo test -d "$path"; then
        sudo install -d -m 0700 -o "$uid" -g "$gid" -- "$path"
    fi
    ensure_fstab_entry_for_snapshots "$base" "$path" "$subvol"
    if ! sudo mount "$path" 2>/dev/null; then
        warn "Failed to mount browser directory $path; skipping isolation."
        return 0
    fi
    if ! current_snapshots_mount_matches_expected "$path" "$subvol" "$base"; then
        warn "Browser subvolume mount verification failed for $path; skipping isolation."
        return 0
    fi
    # Remove the old copy only after the persistent copy and mount are verified.
    drain_browser_original "$hidden"
    sudo sync -f "$top"
    info "Browser data isolated: $path ($subvol)."
}

isolate_browser_profiles() {
    local _name _password uid gid _gecos account_home _shell uid_min uid_max base top role relative canonical
    local -A isolated_paths=()
    uid_min="$(awk '$1 == "UID_MIN" {print $2; exit}' /etc/login.defs)"
    uid_max="$(awk '$1 == "UID_MAX" {print $2; exit}' /etc/login.defs)"
    if ! [[ "$uid_min" =~ ^[0-9]+$ && "$uid_max" =~ ^[0-9]+$ ]]; then
        warn "Cannot determine desktop UID range; skipping browser isolation."
        return 0
    fi
    while IFS=: read -r _name _password uid gid _gecos account_home _shell; do
        (( uid >= uid_min && uid <= uid_max )) || continue
        # One warning per account; skip before home/filesystem validation too.
        if browser_is_running "$uid"; then
            warn "Browser isolation deferred for $_name: close Firefox, Chromium and Chrome, then rerun this script. Existing browser data and mounts are unchanged."
            continue
        fi
        sudo test -d "$account_home" || continue
        account_home="$(sudo realpath -e "$account_home" 2>/dev/null || true)"
        [[ -n "$account_home" ]] || continue
        if [[ "$(sudo stat -f -c %T "$account_home" 2>/dev/null)" != btrfs ]]; then
            warn "Home directory $account_home is not Btrfs; skipping browser isolation."
            continue
        fi
        base="$(get_mount_path TARGET -T "$account_home")"
        top=""
        mount_top_level_for_base "$base" top
        isolated_paths=()
        while read -r role relative; do
            # Never create ~/.mozilla on a fresh account: Firefox detects it as legacy.
            [[ "$role" != firefox_legacy ]] || sudo test -e "$account_home/$relative" || sudo test -L "$account_home/$relative" || continue
            canonical="$(sudo realpath -m "$account_home/$relative")"
            [[ ! -v isolated_paths["$canonical"] ]] || continue
            isolate_browser_directory "$base" "$top" "$uid" "$gid" "$account_home/$relative" "$role"
            isolated_paths["$canonical"]=1
        done <<'BROWSER_PATHS'
chromium .config/chromium
chrome .config/google-chrome
firefox .config/mozilla
firefox_legacy .mozilla
BROWSER_PATHS
        release_temp_mount "$top"
    done < /etc/passwd
}

# Fresh-install stores: create empty top-level subvolumes, then mount them.
# Existing data is not migrated by this installation script.
isolate_empty_store() {
    local path="$1" uid="$2" gid="$3" role="$4" base top="" destination
    local subvol="@store_${uid}_${role}" mode=0700
    # Match the storage mounts prepared by 040_disk_mount.py.
    case "$uid:$role" in
        0:machines|0:portables|0:libvirt) subvol="@var_lib_${role}" ;;
    esac
    [[ "$uid:$role" != 0:libvirt ]] || mode=0755
    sudo mkdir -p -- "$(dirname "$path")"
    path="$(sudo realpath -m "$path")"
    base="$(get_mount_path TARGET -T "$(dirname "$path")")"
    if sudo findmnt -M "$path" >/dev/null; then
        if current_snapshots_mount_matches_expected "$path" "$subvol" "$base"; then
            return 0
        fi
        warn "Store $path is already mounted with unexpected subvolume; skipping isolation."
        return 0
    fi
    if ! dir_is_empty "$path"; then
        warn "Fresh-install store $path contains data; skipping isolation (this script does not migrate existing stores)."
        return 0
    fi
    # tmpfiles can create empty nested machine/portable subvolumes at install.
    # Remove those before mounting so root/home remain flat for rollback.
    if path_is_btrfs_subvolume "$path"; then
        delete_unmounted_subvolume "$path"
    fi
    sudo install -d -m "$mode" -o "$uid" -g "$gid" -- "$path"
    mount_top_level_for_base "$base" top
    destination="$top/$subvol"
    if ! sudo test -e "$destination"; then
        sudo btrfs subvolume create "$destination" >/dev/null
        sudo chmod "$mode" "$destination"
        sudo chown "$uid:$gid" "$destination"
    fi
    ensure_fstab_entry_for_snapshots "$base" "$path" "$subvol"
    if ! sudo mount "$path" 2>/dev/null; then
        warn "Failed to mount fresh-install store $path; skipping isolation."
        release_temp_mount "$top"
        return 0
    fi
    if ! current_snapshots_mount_matches_expected "$path" "$subvol" "$base"; then
        warn "Fresh-install store mount verification failed: $path; skipping isolation."
        release_temp_mount "$top"
        return 0
    fi
    release_temp_mount "$top"
    info "Fresh-install store isolated: $path"
}

isolate_additional_stores() {
    local path role _name _password uid gid _gecos account_home _shell uid_min uid_max
    # Standard daemon paths on the fresh ISO. No running-daemon/config discovery.
    while read -r path role; do
        isolate_empty_store "$path" 0 0 "$role"
    done <<'SYSTEM_STORES'
/var/lib/machines machines
/var/lib/portables portables
/var/lib/docker docker
/var/lib/containerd containerd
/var/lib/containers/storage podman
/var/lib/libvirt libvirt
SYSTEM_STORES
    uid_min="$(awk '$1 == "UID_MIN" {print $2; exit}' /etc/login.defs)"
    uid_max="$(awk '$1 == "UID_MAX" {print $2; exit}' /etc/login.defs)"
    if ! [[ "$uid_min" =~ ^[0-9]+$ && "$uid_max" =~ ^[0-9]+$ ]]; then
        warn "Cannot determine desktop UID range; skipping additional stores."
        return 0
    fi
    while IFS=: read -r _name _password uid gid _gecos account_home _shell; do
        (( uid >= uid_min && uid <= uid_max )) || continue
        sudo test -d "$account_home" || continue
        account_home="$(sudo realpath -e "$account_home" 2>/dev/null || true)"
        [[ -n "$account_home" ]] || continue
        if [[ "$(sudo stat -f -c %T "$account_home" 2>/dev/null)" != btrfs ]]; then
            warn "Home directory $account_home is not Btrfs; skipping additional stores."
            continue
        fi
        for path in .local .local/share .local/share/containers .local/share/libvirt; do
            sudo test -d "$account_home/$path" ||
                sudo install -d -m 0700 -o "$uid" -g "$gid" -- "$account_home/$path"
        done
        isolate_empty_store "$account_home/.cache" "$uid" "$gid" cache
        isolate_empty_store "$account_home/.local/share/docker" "$uid" "$gid" docker
        isolate_empty_store "$account_home/.local/share/containers/storage" "$uid" "$gid" podman
        isolate_empty_store "$account_home/.local/share/libvirt/images" "$uid" "$gid" libvirt_images
    done < /etc/passwd
}

apply_global_btrfs_tuning() {
    local path uuid status seen=' '
    for path in / /home; do
        uuid="$(sudo findmnt -n -e -o UUID -M "$path")" || fatal "Cannot inspect filesystem at $path"
        [[ "$seen" == *" $uuid "* ]] && continue
        seen+="$uuid "
        status="$(sudo btrfs quota status "$path" | awk '/Enabled:/ {print $2}')" || fatal "Cannot inspect Btrfs quota status at $path"
        case "$status" in
            yes) sudo btrfs quota disable "$path" || fatal "Could not disable Btrfs quotas at $path" ;;
            no) ;;
            *) fatal "Unrecognized Btrfs quota status at $path: $status" ;;
        esac
    done
    info "Btrfs quotas are disabled on root and home filesystems."
}

remove_legacy_tmpfiles_overrides() {
    # Older versions forced ordinary directories, pulling machine data into root snapshots.
    local target expected
    for target in /etc/tmpfiles.d/systemd-nspawn.conf /etc/tmpfiles.d/portables.conf; do
        case "$target" in
            */systemd-nspawn.conf) expected='d /var/lib/machines 0700 - - -' ;;
            */portables.conf) expected='d /var/lib/portables 0700 - - -' ;;
        esac
        if sudo test -f "$target" && [[ "$(sudo cat "$target")" == "$expected" ]]; then
            backup_file "$target"
            sudo rm -- "$target"
            info "Removed obsolete tmpfiles override: $target"
        fi
    done
}

enable_snapper_timers() {
    info "Enabling systemd snapper-cleanup.timer to enforce pruning..."
    sudo systemctl enable --now snapper-cleanup.timer || fatal "Could not enable Snapper cleanup timer."

    info "Disabling systemd snapper-timeline.timer to eliminate background wakeups..."
    sudo systemctl disable --now snapper-timeline.timer || fatal "Could not disable Snapper timeline timer."
}

deploy_pair_helper() {
    local target=/usr/local/libexec/dusky-snapshot-pair tmp
    tmp="$(mktemp)"
    ACTIVE_TEMP_FILES+=("$tmp")
    cat <<'PAIR_HELPER' > "$tmp"
#!/usr/bin/env bash
set -euo pipefail
export LC_ALL=C

mkdir -p /run/dusky
exec 9>/run/dusky/dusky.lock
flock -x 9

pair="$(cat /proc/sys/kernel/random/uuid)"

pair_ids() {
    local config="$1"
    snapper --iso --jsonout -c "$config" list |
        python3 -c 'import json, sys
config, pair = sys.argv[1:]
for row in json.load(sys.stdin)[config]:
    if (row.get("userdata") or {}).get("dusky_pair") == pair:
        print(row["number"])' "$config" "$pair"
}

remove_partial_pair() {
    local config ids id incomplete=0
    for config in root home; do
        if ! ids="$(pair_ids "$config")"; then
            printf 'Could not inspect %s snapshots for pair %s\n' "$config" "$pair" >&2
            incomplete=1
            continue
        fi
        while IFS= read -r id; do
            [[ -n "$id" ]] || continue
            snapper -c "$config" delete "$id" || incomplete=1
        done <<< "$ids"
    done
    (( incomplete == 0 ))
}

on_exit() {
    local status=$?
    trap - EXIT
    if (( status != 0 )); then
        remove_partial_pair || printf 'Pair %s needs manual cleanup.\n' "$pair" >&2
    fi
}
trap on_exit EXIT
trap 'exit 130' INT
trap 'exit 143' TERM

snapper -c root create --description 'scheduled daily' \
    --userdata "dusky_pair=${pair},dusky_role=root,dusky_schedule=daily"
snapper -c home create --description 'scheduled daily' \
    --userdata "dusky_pair=${pair},dusky_role=home,dusky_schedule=daily"
root_ids="$(pair_ids root)"
home_ids="$(pair_ids home)"
[[ "$root_ids" =~ ^[0-9]+$ && "$home_ids" =~ ^[0-9]+$ ]]

# The new pair is complete. Retention failures must never remove it.
trap - EXIT
python3 - __SNAPSHOT_LIMIT__ <<'PYTHON_PRUNE'
import json
import subprocess
import sys

limit = int(sys.argv[1])
rows = {}
for config in ("root", "home"):
    result = subprocess.run(
        ["snapper", "--iso", "--jsonout", "-c", config, "list"],
        check=True, capture_output=True, text=True,
    )
    rows[config] = json.loads(result.stdout)[config]

scheduled = {}
for config in ("root", "home"):
    by_pair = {}
    for snapshot in rows[config]:
        metadata = snapshot.get("userdata") or {}
        pair_id = metadata.get("dusky_pair")
        if not pair_id or metadata.get("dusky_role") != config:
            continue
        if metadata.get("dusky_schedule") != "daily" and snapshot.get("description") != "auto 8pm":
            continue
        by_pair.setdefault(pair_id, []).append(snapshot)
    scheduled[config] = by_pair

complete = []
for pair_id in scheduled["root"].keys() | scheduled["home"].keys():
    root = scheduled["root"].get(pair_id, [])
    home = scheduled["home"].get(pair_id, [])
    if len(root) != 1 or len(home) != 1:
        if len(root) > 1 or len(home) > 1:
            print(f"Duplicate scheduled pair tag {pair_id}; inspect it manually.", file=sys.stderr)
            continue
        for config, snapshots in (("root", root), ("home", home)):
            for snapshot in snapshots:
                subprocess.run(["snapper", "-c", config, "delete", str(snapshot["number"])], check=True)
        print(f"Removed incomplete scheduled pair {pair_id}.", file=sys.stderr)
        continue
    complete.append((max(root[0]["date"], home[0]["date"]),
                     root[0]["number"], pair_id,
                     root[0]["number"], home[0]["number"]))

complete.sort()
for _, _, pair_id, root_id, home_id in complete[:-limit]:
    for config, number in (("root", root_id), ("home", home_id)):
        subprocess.run(["snapper", "-c", config, "delete", str(number)], check=True)
PYTHON_PRUNE
PAIR_HELPER
    sed -i "s/__SNAPSHOT_LIMIT__/${SNAPSHOT_RETENTION_LIMIT}/" "$tmp"

    sudo mkdir -p /usr/local/libexec
    if sudo test -f "$target" && sudo cmp -s "$tmp" "$target"; then
        rm -f "$tmp"
        remove_array_value ACTIVE_TEMP_FILES "$tmp"
        return 0
    fi
    backup_file "$target"
    sudo install -m 0755 "$tmp" "${target}.new"
    sudo mv -f -- "${target}.new" "$target"
    sudo sync -f /usr/local/libexec || fatal "Could not sync installed pair helper."
    rm -f "$tmp"
    remove_array_value ACTIVE_TEMP_FILES "$tmp"
}

deploy_custom_timer() {
    info "Deploying custom scheduled snapshot creation timer with gatekeeper..."
    deploy_pair_helper

    local service_file="/etc/systemd/system/dusky_snapshot.service"
    local timer_file="/etc/systemd/system/dusky_snapshot.timer"
    local tmp_service tmp_timer

    tmp_service="$(mktemp)"
    tmp_timer="$(mktemp)"
    ACTIVE_TEMP_FILES+=("$tmp_service" "$tmp_timer")

    # Systemd converts $$ -> $ and %% -> % in ExecCondition.
    cat <<'EOF' > "$tmp_service"
[Unit]
Description=Create Automated Snapper Snapshots
Documentation=man:snapper(8)
After=local-fs.target nss-user-lookup.target
RequiresMountsFor=/.snapshots /home/.snapshots

[Service]
Type=oneshot
CapabilityBoundingSet=CAP_DAC_OVERRIDE CAP_FOWNER CAP_CHOWN CAP_FSETID CAP_SETFCAP CAP_SYS_ADMIN CAP_SYS_MODULE CAP_IPC_LOCK CAP_SYS_NICE
LockPersonality=true
NoNewPrivileges=false
PrivateNetwork=true
ProtectHostname=true
RestrictAddressFamilies=AF_UNIX
RestrictRealtime=true
Nice=19
IOSchedulingClass=idle
CPUSchedulingPolicy=idle
ExecCondition=/usr/bin/bash -c 'if [ -f /var/lib/dusky_snapshot_time ]; then elapsed=$$(( $$(date +%%s) - $$(stat -c %%Y /var/lib/dusky_snapshot_time) )); if [ $$elapsed -lt 72000 ]; then exit 1; fi; fi; exit 0'
ExecStart=/usr/local/libexec/dusky-snapshot-pair
ExecStartPost=/usr/bin/touch /var/lib/dusky_snapshot_time
EOF

    cat << EOF > "$tmp_timer"
[Unit]
Description=Trigger Automated Snapper Snapshots
Documentation=man:snapper(8)

[Timer]
OnCalendar=*-*-* ${SNAPSHOT_TIME}:00
Persistent=true
RandomizedDelaySec=5m

[Install]
WantedBy=timers.target
EOF

    if ! sudo test -f "$service_file" || ! sudo cmp -s "$tmp_service" "$service_file"; then
        backup_file "$service_file"
        atomic_write "$service_file" "$tmp_service"
    fi
    if ! sudo test -f "$timer_file" || ! sudo cmp -s "$tmp_timer" "$timer_file"; then
        backup_file "$timer_file"
        atomic_write "$timer_file" "$tmp_timer"
    fi

    rm -f "$tmp_service" "$tmp_timer"
    remove_array_value ACTIVE_TEMP_FILES "$tmp_service"
    remove_array_value ACTIVE_TEMP_FILES "$tmp_timer"

    sudo systemd-analyze verify --man=no "$service_file" "$timer_file" || fatal "Generated snapshot units failed verification."
    sudo systemctl daemon-reload
    sudo systemctl enable --now dusky_snapshot.timer

    info "Custom scheduled snapshot timer deployed for ${SNAPSHOT_TIME}."
}

preflight_checks() {
    if (( EUID == 0 )); then
        warn "Run as regular user with sudo. Skipping Snapper isolation setup."
        exit 0
    fi

    require_cmd sudo
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
    require_cmd blkid
    require_cmd install
    require_cmd flock
    require_cmd pgrep
    require_cmd systemd-analyze
    require_cmd mount
    require_cmd umount
    require_cmd systemctl

    local root_fs home_fs
    root_fs="$(stat -f -c %T / 2>/dev/null || true)"
    home_fs="$(stat -f -c %T /home 2>/dev/null || true)"

    if [[ "$root_fs" != "btrfs" || "$home_fs" != "btrfs" ]]; then
        warn "Filesystem is not supported (Root: ${root_fs:-unknown}, /home: ${home_fs:-unknown}). Snapper isolation subvolume requires Btrfs. Skipping."
        exit 0
    fi

    require_cmd btrfs

    if ! [[ "$SNAPSHOT_TIME" =~ ^([01][0-9]|2[0-3]):[0-5][0-9]$ ]]; then
        warn "SNAPSHOT_TIME must be in 24-hour HH:MM format. Skipping Snapper isolation setup."
        exit 0
    fi

    if ! [[ "$SNAPSHOT_RETENTION_LIMIT" =~ ^[0-9]+$ ]] || (( SNAPSHOT_RETENTION_LIMIT < 1 )); then
        warn "SNAPSHOT_RETENTION_LIMIT must be a positive integer. Skipping Snapper isolation setup."
        exit 0
    fi

    info "Requesting administrative privileges..."
    if ! sudo -n true 2>/dev/null && ! sudo true 2>/dev/null; then
        warn "Cannot obtain sudo privileges. Skipping Snapper isolation setup."
        exit 0
    fi

    local parent_pid=$$
    (
        while kill -0 "$parent_pid" 2>/dev/null; do
            sudo -n true 2>/dev/null || exit 0
            sleep 60
        done
    ) >/dev/null 2>&1 &
    SUDO_PID=$!
}

preflight_checks
quiesce_snapper

execute "Verify Snapper runtime packages" install_packages
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

# --- CACHE, CONTAINER, VM AND BROWSER ISOLATION ---
execute "Prepare fresh-install cache, container and VM stores" isolate_additional_stores
execute "Isolate browser profiles" isolate_browser_profiles

# --- SYSTEM WIDE OPTIMIZATIONS ---
execute "Apply Global Btrfs Settings" apply_global_btrfs_tuning
execute "Remove old tmpfiles overrides" remove_legacy_tmpfiles_overrides
execute "Enable Snapper Pruning Timers" enable_snapper_timers
execute "Deploy Custom Autonomous Timer" deploy_custom_timer
SETUP_COMPLETE=true
