#!/usr/bin/env bash
# Arch Linux (EFI + Btrfs root/home) | OverlayFS + snap-pac + limine-snapper-sync
# Bash 5.3+

set -Eeuo pipefail
export LC_ALL=C

AUTO_MODE=false
BOOT_SYNC_FLAG=false

for arg in "$@"; do
    case "$arg" in
        --auto) AUTO_MODE=true ;;
        --boot-sync) BOOT_SYNC_FLAG=true ;;
    esac
done

# If manual mode is active, we permit sync features to run (so it prompts you).
# If auto mode is active, we ONLY permit sync features if --boot-sync is passed.
ENABLE_SYNC_FEATURES=false
if [[ "$AUTO_MODE" == false ]] || [[ "$BOOT_SYNC_FLAG" == true ]]; then
    ENABLE_SYNC_FEATURES=true
fi

declare -A BACKED_UP=()
declare -a EFFECTIVE_HOOKS=()
declare -A EFFECTIVE_HOOKS_SET=()

declare -A CACHE_MNT_SOURCE=()
declare -A CACHE_MNT_UUID=()
declare -A CACHE_MNT_OPTS=()

declare -a ACTIVE_TEMP_FILES=()

SUDO_PID=""
ESP_SUFFICIENT_FOR_SYNC=true
ESP_CAPACITY_WARN=""

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
    path="$(sudo btrfs subvolume show / 2>/dev/null | sed -n 's/^[[:space:]]*Path:[[:space:]]*//p' || true)"
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

dep_satisfied() {
    local missing
    missing="$(pacman -T "$1" 2>/dev/null || true)"
    [[ -z "$missing" ]]
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

    for pkg in jdk-openjdk jdk21-openjdk jdk25-openjdk; do
        if pacman -Si "$pkg" >/dev/null 2>&1; then
            provider="$pkg"
            break
        fi
    done
    [[ -n "${provider:-}" ]] || fatal "Java provider required."
    info "Installing $provider"
    sudo pacman -S --needed --noconfirm "$provider"
}

check_esp_capacity() {
    local esp_mnt esp_total_kb
    esp_mnt="$(findmnt -n -e -o TARGET -M /boot 2>/dev/null || findmnt -n -e -o TARGET -M /efi 2>/dev/null || findmnt -n -e -o TARGET -M /boot/efi 2>/dev/null || true)"
    if [[ -n "$esp_mnt" ]]; then
        esp_total_kb="$(df -k "$esp_mnt" 2>/dev/null | awk 'NR==2 {print $2}' || true)"
        if [[ -n "$esp_total_kb" ]] && [[ "$esp_total_kb" =~ ^[0-9]+$ ]]; then
            # We use 1,950,000 KB (~1.95GB) to reliably catch 500MB/1GB ESPs 
            # while allowing standard 2GB partitions with slight filesystem overhead to pass.
            if (( esp_total_kb < 1950000 )); then
                ESP_SUFFICIENT_FOR_SYNC=false
                ESP_CAPACITY_WARN="ESP ($esp_mnt) is under 2GB (~$((esp_total_kb / 1024))MB). Bootloader snapshot sync will be disabled."
            fi
        fi
    fi
}

remove_sync_features() {
    # Silence any active services from background running
    sudo systemctl stop limine-snapper-sync.service 2>/dev/null || true
    sudo systemctl disable limine-snapper-sync.service 2>/dev/null || true
    sudo systemctl stop limine-snapper-sync.path 2>/dev/null || true
    sudo systemctl disable limine-snapper-sync.path 2>/dev/null || true

    local -a pkgs_to_remove=()
    pacman -Q limine-snapper-sync >/dev/null 2>&1 && pkgs_to_remove+=(limine-snapper-sync)
    pacman -Q snap-pac >/dev/null 2>&1 && pkgs_to_remove+=(snap-pac)

    # Completely purge the packages to kill ALPM pacman hooks
    if (( ${#pkgs_to_remove[@]} > 0 )); then
        info "Purging automated sync packages to enforce manual-only policy..."
        
        # Perform a dry-run to detect dependency conflicts silently
        if pacman -Rnsp "${pkgs_to_remove[@]}" >/dev/null 2>&1; then
            sudo pacman -Rns --noconfirm "${pkgs_to_remove[@]}" >/dev/null 2>&1 || true
        else
            warn "Dependency constraint detected. Safely filtering packages to prevent breakage..."
            local pkg
            for pkg in "${pkgs_to_remove[@]}"; do
                # Test each package individually
                if pacman -Rnsp "$pkg" >/dev/null 2>&1; then
                    sudo pacman -Rns --noconfirm "$pkg" >/dev/null 2>&1 || true
                else
                    warn "Skipping removal of '$pkg' (required by other installed packages)."
                    
                    # Neutralize the hostage package by masking its pacman hooks
                    local hook_file base_hook
                    sudo mkdir -p /etc/pacman.d/hooks
                    while IFS= read -r hook_file; do
                        [[ -n "$hook_file" && "$hook_file" == *.hook ]] || continue
                        base_hook="$(basename "$hook_file")"
                        sudo ln -sf /dev/null "/etc/pacman.d/hooks/$base_hook"
                        info "Masked ALPM hook '$base_hook' to functionally neutralize '$pkg'."
                    done < <(pacman -Qlq "$pkg" 2>/dev/null | awk '/^\/usr\/share\/libalpm\/hooks\//')
                    
                    # Neutralize secret systemd drop-ins dynamically
                    local dropin_file dropin_rel_path
                    while IFS= read -r dropin_file; do
                        [[ -n "$dropin_file" ]] || continue
                        dropin_rel_path="$(echo "$dropin_file" | awk -F/ '{print $(NF-1) "/" $NF}')"
                        if [[ -n "$dropin_rel_path" ]]; then
                            sudo mkdir -p "/etc/systemd/system/$(dirname "$dropin_rel_path")"
                            sudo ln -sf /dev/null "/etc/systemd/system/$dropin_rel_path"
                            info "Masked systemd drop-in '$dropin_rel_path' to functionally neutralize '$pkg'."
                        fi
                    done < <(pacman -Qlq "$pkg" 2>/dev/null | awk '/systemd\/system\/.*\.d\/.*\.conf$/')
                fi
            done
        fi
    fi
    
    # Forensic sweep of ESP to prevent hostage capacity issues
    local esp_mnt
    esp_mnt="$(findmnt -n -e -o TARGET -M /boot 2>/dev/null || findmnt -n -e -o TARGET -M /efi 2>/dev/null || findmnt -n -e -o TARGET -M /boot/efi 2>/dev/null || true)"
    if [[ -n "$esp_mnt" ]]; then
        local snap_dirs=("limine-snapshots" "EFI/limine/snapshots" "limine/snapshots")
        local dir
        for dir in "${snap_dirs[@]}"; do
            if sudo test -d "$esp_mnt/$dir"; then
                info "Cleaning up orphaned bootloader snapshots at $esp_mnt/$dir..."
                sudo rm -rf "$esp_mnt/$dir"
            fi
        done
        sudo rm -f "$esp_mnt/limine-snapshots.conf" 2>/dev/null || true
        sudo rm -f "$esp_mnt/EFI/limine/limine-snapshots.conf" 2>/dev/null || true
    fi
    
    sudo rm -f /etc/snap-pac.ini 2>/dev/null || true
    sudo rm -f /etc/limine-snapper-sync.conf 2>/dev/null || true # Cleanup legacy mistakenly created file

    # Safely remove sync configurations from /etc/default/limine without destroying other settings
    if sudo test -f /etc/default/limine; then
        sudo sed -i -E '/^[[:space:]]*ROOT_SUBVOLUME_PATH=/d; /^[[:space:]]*ROOT_SNAPSHOTS_PATH=/d' /etc/default/limine 2>/dev/null || true
    fi
    
    # Reload systemd and kill background GUI notify daemons to apply instantly
    sudo systemctl daemon-reload 2>/dev/null || true
    pkill -f limine-snapper-notify 2>/dev/null || true
}

install_aur_packages() {
    local sync_in=false hook_in=false
    pacman -Q limine-snapper-sync >/dev/null 2>&1 && sync_in=true
    pacman -Q limine-mkinitcpio-hook >/dev/null 2>&1 && hook_in=true

    local -a pkgs=()
    
    if [[ "$ENABLE_SYNC_FEATURES" == true ]] && [[ "$ESP_SUFFICIENT_FOR_SYNC" == true ]]; then
        [[ "$sync_in" == false ]] && pkgs+=(limine-snapper-sync)
    fi

    if [[ "$hook_in" == false ]] && ! command -v limine-update >/dev/null 2>&1; then
        pkgs+=(limine-mkinitcpio-hook)
    fi

    (( ${#pkgs[@]} == 0 )) && return 0

    if ! command -v paru >/dev/null 2>&1 && ! command -v yay >/dev/null 2>&1; then
        fatal "No AUR helper found."
    fi

    ensure_aur_build_prereqs

    if command -v paru >/dev/null 2>&1; then
        paru -S --needed --noconfirm --skipreview "${pkgs[@]}"
    else
        yay -S --needed --noconfirm --answerdiff None --answerclean None --answeredit None "${pkgs[@]}"
    fi
}

install_snap_pac() {
    if pacman -Q snap-pac >/dev/null 2>&1; then
        info "snap-pac is already installed."
        return 0
    fi
    sudo pacman -S --needed --noconfirm snap-pac
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

    sudo mkdir -p /etc/mkinitcpio.conf.d
    backup_file "$managed_file"
    atomic_write "$managed_file" "$tmp"
    rm -f "$tmp"; ACTIVE_TEMP_FILES=("${ACTIVE_TEMP_FILES[@]/$tmp}")

    info "Configured dynamic ${target_hook} injection in ${managed_file}"
}

rebuild_initramfs() { 
    info "Recompiling early boot images to inject overlayfs hooks..."
    
    local shopt_save
    shopt_save=$(shopt -p nullglob || true)
    shopt -s nullglob
    local presets=(/etc/mkinitcpio.d/*.preset)
    eval "$shopt_save"

    if (( ${#presets[@]} > 0 )); then
        sudo mkinitcpio -P < <(echo "n") || true
    else
        info "No mkinitcpio presets found. Delegating generation to limine-update..."
    fi
    
    sudo limine-update || true
}

configure_sync_daemon() {
    local conf_file="/etc/default/limine" root_subvol root_subvol_path tmp
    
    if [[ ! -f "$conf_file" ]]; then
        sudo touch "$conf_file"
        info "Regenerated missing $conf_file."
    fi

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
        info "Configured limine-snapper-sync paths in $conf_file."
    else
        info "limine-snapper-sync paths are already up to date."
    fi
    rm -f "$tmp"; ACTIVE_TEMP_FILES=("${ACTIVE_TEMP_FILES[@]/$tmp}")
    
    # Silently clean up the mistakenly targeted config file from older script versions
    sudo rm -f /etc/limine-snapper-sync.conf 2>/dev/null || true
}

configure_snap_pac() {
    local ini="/etc/snap-pac.ini" tmp
    sudo touch "$ini"
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
    sudo snapper --csv -c "$1" list 2>/dev/null | awk -F',' -v desc="$2" '
        NR == 1 {
            for (i = 1; i <= NF; i++) if ($i == "description") col = i
            next
        }
        col && $col == desc { found = 1; exit }
        END { exit(found ? 0 : 1) }
    '
}

baseline_snapshot_ids_with_cleanup() {
    sudo snapper --csv -c "$1" list 2>/dev/null | awk -F',' -v desc="$2" -v cleanup="$3" '
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

    sudo snapper -c home create -t single -c number -d "snap-pac"
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
            sudo snapper -c "$cfg" delete "$snap_id"
            info "Removed old important baseline snapshot ${cfg}#${snap_id} so it can be recreated with number cleanup."
        done < <(baseline_snapshot_ids_with_cleanup "$cfg" "$desc" "important")
    done

    if ! snapshot_with_description_exists "root" "$desc"; then
        sudo snapper -c root create -t single -c number -d "$desc"
        info "Created baseline root snapshot."
    else
        info "Baseline root snapshot already exists."
    fi

    if ! snapshot_with_description_exists "home" "$desc"; then
        sudo snapper -c home create -t single -c number -d "$desc"
        info "Created baseline home snapshot."
    else
        info "Baseline home snapshot already exists."
    fi
}

enable_services_and_sync() {
    sudo systemctl enable --now snapper-cleanup.timer
    info "Enabled snapper cleanup timer."

    if [[ "$ENABLE_SYNC_FEATURES" == true && "$ESP_SUFFICIENT_FOR_SYNC" == true ]]; then
        sudo systemctl enable --now limine-snapper-sync.service

        if [[ "$(systemctl show -p Result --value limine-snapper-sync.service 2>/dev/null || true)" == "success" ]]; then
            info "Boot menu sync completed via systemd service."
        else
            sudo limine-snapper-sync || true
        fi
    fi
}

restore_sync_features() {
    require_cmd limine-snapper-sync
    
    # Check and unmask any previously neutralized hooks or drop-ins to safely restore functionality
    local _pkg _hook_file _base_hook _dropin_file _dropin_rel_path
    for _pkg in limine-snapper-sync snap-pac; do
        if pacman -Q "$_pkg" >/dev/null 2>&1; then
            while IFS= read -r _hook_file; do
                [[ -n "$_hook_file" && "$_hook_file" == *.hook ]] || continue
                _base_hook="$(basename "$_hook_file")"
                if [[ -L "/etc/pacman.d/hooks/$_base_hook" && "$(readlink "/etc/pacman.d/hooks/$_base_hook")" == "/dev/null" ]]; then
                    sudo rm -f "/etc/pacman.d/hooks/$_base_hook"
                    info "Unmasked ALPM hook '$_base_hook' to restore '$_pkg' functionality."
                fi
            done < <(pacman -Qlq "$_pkg" 2>/dev/null | awk '/^\/usr\/share\/libalpm\/hooks\//')
            
            # Dynamically unmask systemd drop-ins
            while IFS= read -r _dropin_file; do
                [[ -n "$_dropin_file" ]] || continue
                _dropin_rel_path="$(echo "$_dropin_file" | awk -F/ '{print $(NF-1) "/" $NF}')"
                if [[ -n "$_dropin_rel_path" ]]; then
                    if [[ -L "/etc/systemd/system/$_dropin_rel_path" && "$(readlink "/etc/systemd/system/$_dropin_rel_path")" == "/dev/null" ]]; then
                        sudo rm -f "/etc/systemd/system/$_dropin_rel_path"
                        info "Unmasked systemd drop-in '$_dropin_rel_path' to restore '$_pkg' functionality."
                    fi
                fi
            done < <(pacman -Qlq "$_pkg" 2>/dev/null | awk '/systemd\/system\/.*\.d\/.*\.conf$/')
        fi
    done
    sudo systemctl daemon-reload 2>/dev/null || true
}

preflight_checks() {
    (( EUID != 0 )) || fatal "Run as regular user with sudo."
    require_cmd sudo; require_cmd pacman; require_cmd findmnt; require_cmd awk; require_cmd sed; require_cmd grep; require_cmd stat; require_cmd mktemp; require_cmd cmp; require_cmd df
    [[ -d /sys/firmware/efi ]] || fatal "Not booted in EFI mode."
    
    check_esp_capacity
    
    sudo -v || fatal "Cannot obtain sudo privileges."
    (while true; do sudo -n -v 2>/dev/null || exit; sleep 240; done) &
    SUDO_PID=$!
}

preflight_checks
execute "Verify layout" verify_previous_setup
execute "Install AUR packages" install_aur_packages

require_cmd limine-update; require_cmd snapper

# OverlayFS is still needed so you can safely boot manually restored read-only snapshots in the future
execute "Inject OverlayFS hook" configure_mkinitcpio_overlay_hook
execute "Rebuild initramfs" rebuild_initramfs


# Master orchestration block
if [[ "$ENABLE_SYNC_FEATURES" == true && "$ESP_SUFFICIENT_FOR_SYNC" == true ]]; then
    # CRITICAL FIX: Unmasking is a required precondition, not an optional user action.
    # We call it directly so manual mode users can't accidentally break their setup by skipping it.
    restore_sync_features
    execute "Configure sync daemon" configure_sync_daemon
    execute "Install snap-pac" install_snap_pac
    execute "Configure snap-pac" configure_snap_pac
    execute "Ensure home snapshot" ensure_home_snap_pac_snapshot
else
    if [[ "$ENABLE_SYNC_FEATURES" == false ]]; then
        info "Sync features are disabled via flag constraint."
    else
        warn "$ESP_CAPACITY_WARN"
    fi
    execute "Purge sync packages" remove_sync_features
fi

execute "Create baseline snapshots" create_post_config_baseline_snapshot
execute "Enable services and timers" enable_services_and_sync
