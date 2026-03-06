#!/usr/bin/env bash
# Arch Linux (EFI + Btrfs root/home) | OverlayFS + snap-pac + limine-snapper-sync
# Bash 5.3+

set -Eeuo pipefail
export LC_ALL=C
trap 'printf "\n\033[1;31m[FATAL]\033[0m Script failed at line %d. Command: %s\n" "$LINENO" "$BASH_COMMAND" >&2; trap - ERR' ERR

AUTO_MODE=false
[[ "${1:-}" == "--auto" ]] && AUTO_MODE=true

declare -A BACKED_UP=()
declare -a EFFECTIVE_HOOKS=()

SUDO_PID=""
AUR_SYNC_PREEXISTING=false
AUR_LIMINE_HOOK_PREEXISTING=false

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

collect_mkinitcpio_files() {
    local -a files=("/etc/mkinitcpio.conf")
    local file

    shopt -s nullglob
    for file in /etc/mkinitcpio.conf.d/*.conf; do
        files+=("$file")
    done
    shopt -u nullglob

    printf '%s\n' "${files[@]}"
}

get_effective_hooks() {
    local -a files=()
    mapfile -t files < <(collect_mkinitcpio_files)

    EFFECTIVE_HOOKS=()
    mapfile -t EFFECTIVE_HOOKS < <(
        env -i PATH="$PATH" LC_ALL=C bash -O nullglob -c '
            set -e
            for f in "$@"; do
                [[ -f "$f" ]] || continue
                source "$f"
            done
            printf "%s\n" "${HOOKS[@]}"
        ' bash "${files[@]}"
    )

    ((${#EFFECTIVE_HOOKS[@]} > 0)) || fatal "Could not determine the effective mkinitcpio HOOKS array."
}

set_shell_var() {
    local file="$1"
    local key="$2"
    local value="$3"
    local escaped_value

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

set_ini_key() {
    local file="$1"
    local section="$2"
    local key="$3"
    local value="$4"
    local tmp

    sudo touch "$file"
    tmp="$(mktemp)"

    awk -v section="$section" -v key="$key" -v value="$value" '
        function print_key() {
            print key " = " value
            key_written = 1
        }

        BEGIN {
            in_section = 0
            section_found = 0
            key_written = 0
        }

        /^\[[^]]+\][[:space:]]*$/ {
            if (in_section && !key_written) {
                print_key()
            }

            current = $0
            gsub(/^\[/, "", current)
            gsub(/\]$/, "", current)

            in_section = (current == section)
            if (in_section) {
                section_found = 1
                key_written = 0
            }

            print
            next
        }

        {
            if (in_section && $0 ~ "^[[:space:]]*" key "[[:space:]]*=") {
                if (!key_written) {
                    print_key()
                }
                next
            }
            print
        }

        END {
            if (in_section && !key_written) {
                print_key()
            } else if (!section_found) {
                print ""
                print "[" section "]"
                print key " = " value
            }
        }
    ' "$file" > "$tmp"

    sudo install -m 0644 "$tmp" "$file"
    rm -f "$tmp"
}

dep_satisfied() {
    local dep="$1"
    [[ -z "$(pacman -T "$dep" 2>/dev/null || true)" ]]
}

choose_java_provider() {
    local pkg

    for pkg in jdk-openjdk jdk21-openjdk; do
        if pacman -Si "$pkg" >/dev/null 2>&1; then
            printf '%s\n' "$pkg"
            return 0
        fi
    done

    return 1
}

ensure_aur_build_prereqs() {
    local need_java=false
    local dep provider

    for dep in 'java-runtime>=21' 'java-environment>=21'; do
        if ! dep_satisfied "$dep"; then
            need_java=true
            break
        fi
    done

    [[ "$need_java" == true ]] || return 0

    provider="$(choose_java_provider)" || fatal "A Java provider for java-environment>=21 is required, but no suitable repo package was found."
    info "Installing $provider to satisfy Java build dependencies for the AUR package."
    sudo pacman -S --needed --noconfirm "$provider"
}

install_aur_packages() {
    AUR_SYNC_PREEXISTING=false
    AUR_LIMINE_HOOK_PREEXISTING=false

    pacman -Q limine-snapper-sync >/dev/null 2>&1 && AUR_SYNC_PREEXISTING=true
    pacman -Q limine-mkinitcpio-hook >/dev/null 2>&1 && AUR_LIMINE_HOOK_PREEXISTING=true

    if ! command -v paru >/dev/null 2>&1 && ! command -v yay >/dev/null 2>&1; then
        if [[ "$AUR_SYNC_PREEXISTING" == true && ( "$AUR_LIMINE_HOOK_PREEXISTING" == true || -x /usr/bin/limine-update ) ]]; then
            info "Required AUR packages are already installed."
            return 0
        fi
        fatal "No supported AUR helper found. Install paru or yay first."
    fi

    ensure_aur_build_prereqs

    local -a pkgs=(limine-snapper-sync)
    command -v limine-update >/dev/null 2>&1 || pkgs+=(limine-mkinitcpio-hook)

    if command -v paru >/dev/null 2>&1; then
        paru -S --needed --noconfirm --skipreview "${pkgs[@]}"
    else
        yay -S --needed --noconfirm \
            --answerdiff None \
            --answerclean None \
            --answeredit None \
            "${pkgs[@]}"
    fi
}

install_snap_pac() {
    sudo pacman -S --needed --noconfirm snap-pac
}

verify_previous_setup() {
    # Root checks
    findmnt -M /.snapshots >/dev/null 2>&1 || fatal "/.snapshots is not mounted. Run the Snapper isolation script first."
    sudo snapper -c root get-config >/dev/null 2>&1 || fatal "Snapper root config is missing or unusable."

    local mounted_opts mounted_subvol
    mounted_opts="$(findmnt -M /.snapshots -no OPTIONS 2>/dev/null || true)"
    mounted_subvol="$(extract_subvol "$mounted_opts" || true)"
    mounted_subvol="${mounted_subvol#/}"

    [[ "$mounted_subvol" == "@snapshots" ]] || fatal "/.snapshots is mounted, but not from subvol=/@snapshots"

    # Home checks
    findmnt -M /home/.snapshots >/dev/null 2>&1 || fatal "/home/.snapshots is not mounted. Run the Snapper isolation script first."
    sudo snapper -c home get-config >/dev/null 2>&1 || fatal "Snapper home config is missing or unusable."

    local home_mounted_opts home_mounted_subvol
    home_mounted_opts="$(findmnt -M /home/.snapshots -no OPTIONS 2>/dev/null || true)"
    home_mounted_subvol="$(extract_subvol "$home_mounted_opts" || true)"
    home_mounted_subvol="${home_mounted_subvol#/}"

    [[ "$home_mounted_subvol" == "@home_snapshots" ]] || fatal "/home/.snapshots is mounted, but not from subvol=/@home_snapshots"

    info "Verified Snapper isolated layout for root and home."
}

choose_overlay_hook() {
    local hook
    for hook in "${EFFECTIVE_HOOKS[@]}"; do
        if [[ "$hook" == "systemd" ]]; then
            printf '%s\n' "sd-btrfs-overlayfs"
            return 0
        fi
    done

    printf '%s\n' "btrfs-overlayfs"
}

verify_overlay_hook_available() {
    local target_hook="$1"

    [[ -f "/usr/lib/initcpio/install/${target_hook}" ]] || fatal "The mkinitcpio hook ${target_hook} is not installed on this system."
}

configure_mkinitcpio_overlay_hook() {
    local target_hook managed_file current_hook hook
    local found_filesystems=false
    local -a filtered_hooks=()
    local -a final_hooks=()
    local tmp

    get_effective_hooks
    target_hook="$(choose_overlay_hook)"
    verify_overlay_hook_available "$target_hook"

    for hook in "${EFFECTIVE_HOOKS[@]}"; do
        case "$hook" in
            btrfs-overlayfs|sd-btrfs-overlayfs)
                continue
                ;;
        esac

        filtered_hooks+=("$hook")
        [[ "$hook" == "filesystems" ]] && found_filesystems=true
    done

    [[ "$found_filesystems" == true ]] || fatal "'filesystems' is missing from mkinitcpio HOOKS."

    for hook in "${filtered_hooks[@]}"; do
        final_hooks+=("$hook")
        if [[ "$hook" == "filesystems" ]]; then
            final_hooks+=("$target_hook")
        fi
    done

    managed_file="/etc/mkinitcpio.conf.d/zz-limine-overlayfs.conf"
    current_hook=""
    if [[ -f "$managed_file" ]]; then
        current_hook="$(grep -E '^[[:space:]]*HOOKS=' "$managed_file" 2>/dev/null || true)"
    fi

    tmp="$(mktemp)"
    {
        printf '# Managed by limine + snapper integration setup\n'
        printf 'HOOKS=('
        printf '%s' "${final_hooks[0]}"
        local i
        for ((i = 1; i < ${#final_hooks[@]}; i++)); do
            printf ' %s' "${final_hooks[i]}"
        done
        printf ')\n'
    } > "$tmp"

    if [[ -f "$managed_file" ]] && sudo cmp -s "$tmp" "$managed_file" 2>/dev/null; then
        rm -f "$tmp"
        info "${target_hook} is already configured in ${managed_file}"
        return 0
    fi

    backup_file "$managed_file"
    sudo install -D -m 0644 "$tmp" "$managed_file"
    rm -f "$tmp"

    info "Configured ${target_hook} in ${managed_file}"
}

rebuild_initramfs() {
    sudo limine-update
}

configure_sync_daemon() {
    local conf_file="/etc/limine-snapper-sync.conf"
    local root_subvol root_subvol_path

    [[ -f "$conf_file" ]] || fatal "$conf_file was not found. limine-snapper-sync is probably not installed."

    root_subvol="$(extract_subvol "$(findmnt -no OPTIONS /)" || true)"
    if [[ -n "$root_subvol" ]]; then
        root_subvol_path="/${root_subvol#/}"
    else
        root_subvol_path="/"
    fi

    backup_file "$conf_file"
    set_shell_var "$conf_file" ROOT_SUBVOLUME_PATH "$root_subvol_path"
    set_shell_var "$conf_file" ROOT_SNAPSHOTS_PATH "/@snapshots"

    info "Configured limine-snapper-sync paths."
}

configure_snap_pac() {
    local ini="/etc/snap-pac.ini"

    backup_file "$ini"
    set_ini_key "$ini" root snapshot yes
    set_ini_key "$ini" home snapshot yes

    info "Configured snap-pac for root and home."
}

baseline_snapshot_exists() {
    local config="$1"
    local desc="$2"

    sudo snapper -c "$config" list | awk -F'|' -v desc="$desc" '
        NF >= 7 {
            field = $7
            gsub(/^[[:space:]]+|[[:space:]]+$/, "", field)
            if (field == desc) {
                found = 1
            }
        }
        END {
            exit(found ? 0 : 1)
        }
    '
}

create_post_config_baseline_snapshot() {
    local desc="Baseline after Limine + Snapper integration"

    if ! baseline_snapshot_exists "root" "$desc"; then
        sudo snapper -c root create -t single -c important -d "$desc"
        info "Created baseline root snapshot."
    else
        info "Baseline root snapshot already exists."
    fi

    if ! baseline_snapshot_exists "home" "$desc"; then
        sudo snapper -c home create -t single -c important -d "$desc"
        info "Created baseline home snapshot."
    else
        info "Baseline home snapshot already exists."
    fi
}

enable_services_and_sync() {
    sudo systemctl daemon-reload
    sudo systemctl enable --now snapper-cleanup.timer
    sudo systemctl enable --now limine-snapper-sync.service

    sudo limine-snapper-sync
    info "Boot menu sync completed."
}

preflight_checks() {
    (( EUID != 0 )) || fatal "Run this script as a regular user with sudo privileges, not as root."

    require_cmd sudo
    require_cmd pacman
    require_cmd findmnt
    require_cmd awk
    require_cmd sed
    require_cmd grep
    require_cmd stat
    require_cmd mktemp
    require_cmd cmp
    require_cmd date

    [[ -d /sys/firmware/efi ]] || fatal "System is not booted in EFI mode."
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
execute "Verify Snapper isolated snapshot layout" verify_previous_setup
execute "Install limine-snapper-sync and related AUR packages" install_aur_packages
require_cmd limine-update
require_cmd limine-snapper-sync
require_cmd snapper
execute "Inject the correct OverlayFS hook into mkinitcpio" configure_mkinitcpio_overlay_hook
execute "Rebuild initramfs and Limine config" rebuild_initramfs
execute "Configure limine-snapper-sync" configure_sync_daemon
execute "Install snap-pac from the repo" install_snap_pac
execute "Configure snap-pac" configure_snap_pac
execute "Create a post-configuration baseline snapshot" create_post_config_baseline_snapshot
execute "Enable cleanup + sync services and perform initial sync" enable_services_and_sync
