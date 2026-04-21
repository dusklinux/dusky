#!/usr/bin/env bash
# Arch Linux (EFI + Btrfs root/home) | OverlayFS + snap-pac + limine-snapper-sync
# CHROOT DEPLOYMENT EDITION - FORENSICALLY AUDITED

set -Eeuo pipefail
export LC_ALL=C

AUTO_MODE=false
[[ "${1:-}" == "--auto" ]] && AUTO_MODE=true

declare -A BACKED_UP=()
declare -a EFFECTIVE_HOOKS=()
declare -A EFFECTIVE_HOOKS_SET=()
declare -a ACTIVE_TEMP_FILES=()

cleanup() {
    local f
    for f in "${ACTIVE_TEMP_FILES[@]}"; do
        [[ -n "$f" && -f "$f" ]] && rm -f "$f" 2>/dev/null || true
    done
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
    cp -a -- "$file" "${file}.bak.${stamp}"
    BACKED_UP["$file"]=1
    info "Backup created: ${file}.bak.${stamp}"
}

require_cmd() {
    command -v "$1" >/dev/null 2>&1 || fatal "Required command not found: $1"
}

atomic_write() {
    local target="$1" src="$2" target_dir tmp_target
    target_dir="$(dirname "$target")"
    tmp_target="$(mktemp "${target_dir}/.tmp.XXXXXX")"
    ACTIVE_TEMP_FILES+=("$tmp_target")
    cp "$src" "$tmp_target"
    chmod 0644 "$tmp_target"
    mv "$tmp_target" "$target"
    ACTIVE_TEMP_FILES=("${ACTIVE_TEMP_FILES[@]/$tmp_target}")
    sync -f "$target_dir" 2>/dev/null || true
}

extract_subvol() {
    if [[ "$1" =~ subvol=([^,]+) ]]; then
        printf '%s\n' "${BASH_REMATCH[1]#/}"
        return 0
    fi
    return 1
}

get_root_subvolume_path() {
    local findmnt_out path
    findmnt_out="$(findmnt -n -e -o OPTIONS -M / 2>/dev/null || true)"
    path="$(extract_subvol "$findmnt_out" || true)"
    [[ -n "$path" ]] && { printf '%s\n' "$path"; return 0; }

    require_cmd btrfs
    path="$(btrfs subvolume show / 2>/dev/null | sed -n 's/^[[:space:]]*Path:[[:space:]]*//p' || true)"
    path="${path#/}"
    case "$path" in ""|"<FS_TREE>"|"/") return 1 ;; esac
    printf '%s\n' "$path"
}

collect_mkinitcpio_files() {
    local -a files=("/etc/mkinitcpio.conf")
    local file shopt_save
    shopt_save=$(shopt -p nullglob || true); shopt -s nullglob
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
            for f in "$@"; do [[ -f "$f" ]] || continue; source "$f" >/dev/null 2>&1; done
            [[ "$(declare -p HOOKS 2>/dev/null)" =~ "declare -a" ]] || HOOKS=(${HOOKS})
            for h in "${HOOKS[@]}"; do echo "$h"; done
        ' bash "${files[@]}"
    )"

    [[ -n "$hooks_str" ]] || fatal "Could not determine effective HOOKS."
    mapfile -t EFFECTIVE_HOOKS <<< "$hooks_str"
    local hook
    for hook in "${EFFECTIVE_HOOKS[@]}"; do EFFECTIVE_HOOKS_SET["$hook"]=1; done
}

install_aur_packages() {
    local sync_in=false hook_in=false
    pacman -Q limine-snapper-sync >/dev/null 2>&1 && sync_in=true
    pacman -Q limine-mkinitcpio-hook >/dev/null 2>&1 && hook_in=true

    local -a pkgs=()
    [[ "$sync_in" == false ]] && pkgs+=(limine-snapper-sync)
    if [[ "$hook_in" == false ]] && ! command -v limine-update >/dev/null 2>&1; then
        pkgs+=(limine-mkinitcpio-hook)
    fi

    (( ${#pkgs[@]} == 0 )) && return 0

    info "Installing needed packages from offline repository..."
    pacman -S --needed --noconfirm "${pkgs[@]}"
}

install_snap_pac() {
    if pacman -Q snap-pac >/dev/null 2>&1; then
        info "snap-pac is already installed."
        return 0
    fi
    pacman -S --needed --noconfirm snap-pac
}

verify_previous_setup() {
    local root_opts home_opts
    root_opts="$(findmnt -n -e -o OPTIONS -M /.snapshots 2>/dev/null || true)"
    [[ "$(extract_subvol "$root_opts" || true)" == "@snapshots" ]] || fatal "/.snapshots is not mounted from @snapshots."

    home_opts="$(findmnt -n -e -o OPTIONS -M /home/.snapshots 2>/dev/null || true)"
    [[ "$(extract_subvol "$home_opts" || true)" == "@home_snapshots" ]] || fatal "/home/.snapshots is not mounted from @home_snapshots."

    info "Verified Snapper isolated layout for root and home."
}

configure_mkinitcpio_overlay_hook() {
    local target_hook managed_file tmp
    get_effective_hooks

    target_hook="btrfs-overlayfs"
    [[ -v EFFECTIVE_HOOKS_SET["systemd"] ]] && target_hook="sd-btrfs-overlayfs"

    [[ -f "/usr/lib/initcpio/install/${target_hook}" ]] || fatal "Hook ${target_hook} missing."
    [[ -v EFFECTIVE_HOOKS_SET["filesystems"] ]] || fatal "'filesystems' missing from HOOKS."

    managed_file="/etc/mkinitcpio.conf.d/zz-limine-overlayfs.conf"
    tmp="$(mktemp)"
    ACTIVE_TEMP_FILES+=("$tmp")

    cat <<EOF > "$tmp"
# Managed by limine + snapper integration setup
if [[ " \${HOOKS[*]} " != *" ${target_hook} "* ]]; then
    _new_hooks=()
    for _h in "\${HOOKS[@]}"; do
        _new_hooks+=("\$_h")
        if [[ "\$_h" == "filesystems" ]]; then
            _new_hooks+=("${target_hook}")
        fi
    done
    HOOKS=("\${_new_hooks[@]}")
    unset _new_hooks _h
fi
EOF

    if [[ -f "$managed_file" ]] && cmp -s "$tmp" "$managed_file"; then
        rm -f "$tmp"; ACTIVE_TEMP_FILES=("${ACTIVE_TEMP_FILES[@]/$tmp}")
        info "${target_hook} is already configured in ${managed_file}."
        return 0
    fi

    mkdir -p /etc/mkinitcpio.conf.d
    backup_file "$managed_file"
    atomic_write "$managed_file" "$tmp"
    rm -f "$tmp"; ACTIVE_TEMP_FILES=("${ACTIVE_TEMP_FILES[@]/$tmp}")

    info "Configured dynamic ${target_hook} injection in ${managed_file}"
}

rebuild_initramfs() { 
    info "Recompiling early boot images to inject overlayfs hooks..."
    mkinitcpio -P < <(echo "n")
    limine-update || true
}

configure_sync_daemon() {
    local conf_file="/etc/limine-snapper-sync.conf" root_subvol root_subvol_path tmp
    [[ -f "$conf_file" ]] || fatal "$conf_file not found."

    root_subvol="$(get_root_subvolume_path || true)"
    root_subvol_path="${root_subvol:+/$root_subvol}"
    root_subvol_path="${root_subvol_path:-/}"

    tmp="$(mktemp)"; ACTIVE_TEMP_FILES+=("$tmp")
    awk -v rsp="$root_subvol_path" -v snap="/@snapshots" '
        BEGIN { wr=0; ws=0 }
        /^[[:space:]]*ROOT_SUBVOLUME_PATH=/ { print "ROOT_SUBVOLUME_PATH=\"" rsp "\""; wr=1; next }
        /^[[:space:]]*ROOT_SNAPSHOTS_PATH=/ { print "ROOT_SNAPSHOTS_PATH=\"" snap "\""; ws=1; next }
        { print $0 }
        END { if (!wr) print "ROOT_SUBVOLUME_PATH=\"" rsp "\""; if (!ws) print "ROOT_SNAPSHOTS_PATH=\"" snap "\"" }
    ' "$conf_file" > "$tmp"

    if ! cmp -s "$tmp" "$conf_file"; then
        backup_file "$conf_file"
        atomic_write "$conf_file" "$tmp"
        info "Configured limine-snapper-sync paths."
    else
        info "limine-snapper-sync paths are already up to date."
    fi
    rm -f "$tmp"; ACTIVE_TEMP_FILES=("${ACTIVE_TEMP_FILES[@]/$tmp}")
}

configure_snap_pac() {
    local ini="/etc/snap-pac.ini" tmp
    touch "$ini"
    tmp="$(mktemp)"; ACTIVE_TEMP_FILES+=("$tmp")

    awk '
        BEGIN { sec = "" }
        /^[[:space:]]*\[.*\][[:space:]]*$/ {
            sec = $0; sub(/^[[:space:]]*\[/, "", sec); sub(/\][[:space:]]*$/, "", sec)
            print $0
            if (sec == "root" || sec == "home") { print "snapshot = yes"; seen[sec] = 1 }
            next
        }
        /^[[:space:]]*snapshot[[:space:]]*=/ { if (sec == "root" || sec == "home") next }
        { print $0 }
        END {
            if (!seen["root"]) { print "\n[root]\nsnapshot = yes" }
            if (!seen["home"]) { print "\n[home]\nsnapshot = yes" }
        }
    ' "$ini" > "$tmp"

    if ! cmp -s "$tmp" "$ini"; then
        backup_file "$ini"
        atomic_write "$ini" "$tmp"
        info "Configured snap-pac for root and home."
    else
        info "snap-pac is already configured correctly."
    fi
    rm -f "$tmp"; ACTIVE_TEMP_FILES=("${ACTIVE_TEMP_FILES[@]/$tmp}")
}

snapshot_with_description_exists() {
    # CHROOT FIX: Inject --no-dbus
    snapper --no-dbus --csv -c "$1" list 2>/dev/null | awk -F',' -v desc="$2" '
        NR == 1 {
            for (i = 1; i <= NF; i++) if ($i == "description") col = i
            next
        }
        col && $col == desc { found = 1; exit }
        END { exit(found ? 0 : 1) }
    '
}

baseline_snapshot_ids_with_cleanup() {
    # CHROOT FIX: Inject --no-dbus
    snapper --no-dbus --csv -c "$1" list 2>/dev/null | awk -F',' -v desc="$2" -v cleanup="$3" '
        NR == 1 {
            for (i = 1; i <= NF; i++) {
                if ($i == "number") num_col = i
                else if ($i == "cleanup") cleanup_col = i
                else if ($i == "description") desc_col = i
            }
            next
        }
        num_col && cleanup_col && desc_col &&
        $desc_col == desc &&
        $cleanup_col == cleanup &&
        $num_col ~ /^[0-9]+$/ &&
        $num_col != "0" {
            print $num_col
        }
    '
}

ensure_home_snap_pac_snapshot() {
    if ! snapshot_with_description_exists root "snap-pac"; then
        info "No root snap-pac snapshot detected; nothing to backfill for home."
        return 0
    fi

    if snapshot_with_description_exists home "snap-pac"; then
        info "Home already has a snap-pac snapshot."
        return 0
    fi

    # CHROOT FIX: Inject --no-dbus
    snapper --no-dbus -c home create -t single -c number -d "snap-pac"
    info "Created missing home snap-pac snapshot."
}

create_post_config_baseline_snapshot() {
    local desc="Baseline after Limine + Snapper integration"
    local cfg snap_id

    for cfg in root home; do
        while IFS= read -r snap_id; do
            [[ -n "$snap_id" ]] || continue
            [[ "$snap_id" =~ ^[0-9]+$ ]] || fatal "Unexpected non-numeric snapshot id parsed for ${cfg}: ${snap_id}"
            [[ "$snap_id" == "0" ]] && continue
            # CHROOT FIX: Inject --no-dbus
            snapper --no-dbus -c "$cfg" delete "$snap_id"
            info "Removed old important baseline snapshot ${cfg}#${snap_id} so it can be recreated with number cleanup."
        done < <(baseline_snapshot_ids_with_cleanup "$cfg" "$desc" "important")
    done

    if ! snapshot_with_description_exists "root" "$desc"; then
        # CHROOT FIX: Inject --no-dbus
        snapper --no-dbus -c root create -t single -c number -d "$desc"
        info "Created baseline root snapshot."
    else
        info "Baseline root snapshot already exists."
    fi

    if ! snapshot_with_description_exists "home" "$desc"; then
        # CHROOT FIX: Inject --no-dbus
        snapper --no-dbus -c home create -t single -c number -d "$desc"
        info "Created baseline home snapshot."
    else
        info "Baseline home snapshot already exists."
    fi
}

enable_services_and_sync() {
    # Systemd init is not running in chroot, so we only *enable* them for next boot. 
    # Do NOT use --now.
    systemctl enable snapper-cleanup.timer
    systemctl enable limine-snapper-sync.service

    info "Manually running boot menu sync for offline chroot environment..."
    limine-snapper-sync || true
}

preflight_checks() {
    require_cmd pacman; require_cmd findmnt; require_cmd awk; require_cmd sed; require_cmd grep; require_cmd stat; require_cmd mktemp; require_cmd cmp
    [[ -d /sys/firmware/efi ]] || fatal "Not booted in EFI mode."
}

preflight_checks
execute "Verify layout" verify_previous_setup
execute "Install AUR packages" install_aur_packages
require_cmd limine-update; require_cmd limine-snapper-sync; require_cmd snapper
execute "Inject OverlayFS hook" configure_mkinitcpio_overlay_hook
execute "Rebuild initramfs" rebuild_initramfs
execute "Configure sync daemon" configure_sync_daemon
execute "Install snap-pac" install_snap_pac
execute "Configure snap-pac" configure_snap_pac
execute "Ensure home snap-pac snapshot" ensure_home_snap_pac_snapshot
execute "Create baseline snapshot" create_post_config_baseline_snapshot
execute "Enable services" enable_services_and_sync
