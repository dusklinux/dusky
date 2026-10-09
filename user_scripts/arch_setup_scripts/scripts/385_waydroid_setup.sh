#!/usr/bin/env bash
#d: Set up Waydroid for Android apps

set -Eeuo pipefail
shopt -s inherit_errexit nullglob

readonly C_RESET=$'\033[0m' C_GREEN=$'\033[1;32m' C_BLUE=$'\033[1;34m'
readonly C_RED=$'\033[1;31m' C_YELLOW=$'\033[1;33m'
readonly DEST_DIR=/etc/waydroid-extra/images
readonly WORK_DIR=/var/lib/waydroid
readonly SERVICE=waydroid-container.service
STAGE_DIR=''
TEMP_DIR=''

log_info()    { printf '%s[INFO]%s %s\n' "$C_BLUE" "$C_RESET" "$*"; }
log_success() { printf '%s[OK]%s %s\n' "$C_GREEN" "$C_RESET" "$*"; }
log_warn()    { printf '%s[WARN]%s %s\n' "$C_YELLOW" "$C_RESET" "$*"; }
log_error()   { printf '%s[ERROR]%s %s\n' "$C_RED" "$C_RESET" "$*" >&2; exit 1; }

cleanup() {
    local status=$?
    trap - EXIT
    [[ -z "$STAGE_DIR" ]] || rm -rf -- "$STAGE_DIR" || status=1
    [[ -z "$TEMP_DIR" ]] || rm -rf -- "$TEMP_DIR" || status=1
    exit "$status"
}

ask_yes() {
    local answer
    read -r -p "$1 [y/N] " answer || return 1
    [[ "$answer" == [Yy] ]]
}

expand_user_path() {
    # Quoted tildes are intentional: expand against the invoking user's home.
    # shellcheck disable=SC2088
    case "$1" in
        '~') printf '%s\n' "$REAL_HOME" ;;
        '~/'*) printf '%s/%s\n' "$REAL_HOME" "${1:2}" ;;
        *) printf '%s\n' "$1" ;;
    esac
}

ensure_packages() {
    local package
    local -a missing=()
    for package in "$@"; do
        pacman -Qq "$package" >/dev/null 2>&1 || missing+=("$package")
    done
    if (( ${#missing[@]} )); then
        pacman -S --noconfirm --needed "${missing[@]}"
    fi
}

images_ready() {
    [[ -f "$DEST_DIR/system.img" && -s "$DEST_DIR/system.img" &&
       -f "$DEST_DIR/vendor.img" && -s "$DEST_DIR/vendor.img" ]]
}

# Auto-select only an unambiguous regular file; never silently choose an old ZIP.
select_image() {
    local directory=$1 kind=$2 candidate input
    local -a matches=()
    local -n selected=$3
    for candidate in "$directory/$kind.img" "$directory/"*"$kind"*.zip; do
        [[ -f "$candidate" ]] && matches+=("$candidate")
    done
    if (( ${#matches[@]} == 1 )); then
        selected=${matches[0]}
    else
        if (( ${#matches[@]} > 1 )); then
            log_warn "Multiple $kind files found; select the intended image."
            printf '  %s\n' "${matches[@]}"
        fi
        read -r -e -p "Full path to $kind.img or its ZIP archive: " input || log_error 'Input ended.'
        selected=$(expand_user_path "$input")
    fi
    [[ -f "$selected" && -s "$selected" ]] || log_error "Missing or empty image: $selected"
    # Absolute paths keep leading '-' and archive names independent of the cwd.
    selected=$(realpath -e -- "$selected")
}

stage_image() {
    local input=$1 name=$2 listing member internal=''
    local count=0 output
    output=$(mktemp "$STAGE_DIR/.image.XXXXXXXX")
    case "$input" in
        *.zip)
            listing=$(unzip -Z -1 "$input") || log_error "Cannot list archive: $input"
            while IFS= read -r member; do
                if [[ "${member##*/}" == "$name" ]]; then
                    internal=$member
                    ((++count))
                fi
            done <<< "$listing"
            (( count == 1 )) || log_error "Archive must contain exactly one $name: $input"
            # unzip treats member arguments as patterns; refuse ambiguous names.
            [[ "$internal" != *[\[\]\*\?]* ]] || log_error "Unsupported ZIP member name: $internal"
            log_info "Extracting $name from ${input##*/}..."
            unzip -p "$input" "$internal" > "$output" || log_error "Extraction/CRC check failed: $input"
            ;;
        *.img)
            log_info "Copying $name (original retained)..."
            cp --reflink=auto --sparse=auto -- "$input" "$output"
            ;;
        *) log_error "Expected a .zip or .img file: $input" ;;
    esac
    [[ -s "$output" ]] || log_error "Extracted image is empty: $name"
    chmod 0644 -- "$output"
    mv -fT -- "$output" "$STAGE_DIR/$name"
}

import_images() {
    local downloads source_dir input system_file vendor_file directory
    local real_user=${SUDO_USER:-${DOAS_USER:-root}}
    local passwd_entry
    passwd_entry=$(getent passwd "$real_user") || log_error "Cannot resolve user: $real_user"
    IFS=: read -r _ _ _ _ _ REAL_HOME _ <<< "$passwd_entry"
    [[ -d "$REAL_HOME" ]] || log_error "User home does not exist: $REAL_HOME"
    downloads="$REAL_HOME/Downloads"
    if command -v xdg-user-dir >/dev/null; then
        downloads=$(sudo -H -u "$real_user" -- xdg-user-dir DOWNLOAD)
        [[ -d "$downloads" ]] || downloads="$REAL_HOME/Downloads"
    fi
    source_dir=$downloads
    for directory in "$downloads/Waydroid" "$downloads"; do
        [[ -d "$directory" ]] || continue
        for input in "$directory/system.img" "$directory/"*system*.zip; do
            if [[ -f "$input" ]]; then
                source_dir=$directory
                break 2
            fi
        done
    done
    log_info "Use matching system/vendor images for the host architecture ($(uname -m))."
    printf '%s\n' \
        'System: https://sourceforge.net/projects/waydroid/files/images/system/lineage/' \
        'Vendor: https://sourceforge.net/projects/waydroid/files/images/vendor/'
    read -r -e -p "Directory containing downloaded images [$source_dir]: " input || log_error 'Input ended.'
    source_dir=$(expand_user_path "${input:-$source_dir}")
    [[ -d "$source_dir" ]] || log_error "Directory does not exist: $source_dir"
    select_image "$source_dir" system system_file
    select_image "$source_dir" vendor vendor_file

    install -d -m 0755 -- "$DEST_DIR"
    STAGE_DIR=$(mktemp -d "${DEST_DIR%/*}/.images-setup.XXXXXXXX")
    # Preserve auxiliary files without copying existing multi-gigabyte images.
    # stage_image replaces these hard links rather than writing through them.
    cp -al -- "$DEST_DIR/." "$STAGE_DIR"
    stage_image "$system_file" system.img
    stage_image "$vendor_file" vendor.img
    # Commit the image pair and its recovery request together. An interrupted
    # or failed exchange must not request reinitialization of the old pair.
    : > "$STAGE_DIR/.setup-init-pending"
    # Keep mounted images intact until both replacement files have passed extraction.
    systemctl stop "$SERVICE"
    # Exchange the directories in one atomic rename: never expose a mixed pair.
    if ! mv --exchange --no-copy -T -- "$STAGE_DIR" "$DEST_DIR"; then
        log_error 'Image exchange failed; the existing image pair was retained.'
    fi
    rm -rf -- "$STAGE_DIR"
    STAGE_DIR=''
    log_success 'Both images installed; source files retained.'
}

# Use Waydroid's persistent INI overrides, not its generated base properties.
# Print whether initialization and a property refresh are needed, respectively.
configure_waydroid() {
    python3 - "$WORK_DIR" "$DEST_DIR" <<'PY'
import configparser
import os
from pathlib import Path
import sys
import tempfile

work, images = map(Path, sys.argv[1:])
path = work / 'waydroid.cfg'
cfg = configparser.ConfigParser()
if path.exists():
    with path.open(encoding='utf-8') as handle:
        cfg.read_file(handle)
required_keys = ('arch', 'vendor_type', 'binder', 'vndbinder', 'hwbinder',
                 'system_ota', 'vendor_ota')
lxc_config = work / 'lxc/waydroid/config'
needs_init = (
    any(not cfg.get('waydroid', key, fallback='').strip() for key in required_keys)
    or Path(cfg.get('waydroid', 'images_path', fallback='')).resolve() != images.resolve()
    or not (work / 'rootfs').is_dir()
    or not lxc_config.is_file()
    or lxc_config.stat().st_size == 0
)
key = 'persist.waydroid.multi_windows'
changed = cfg.get('properties', key, fallback='') != 'true'
if changed:
    if not cfg.has_section('properties'):
        cfg.add_section('properties')
    cfg.set('properties', key, 'true')
    work.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(mode='w', encoding='utf-8', dir=work, delete=False) as handle:
        temporary = Path(handle.name)
        try:
            cfg.write(handle)
            handle.flush()
            temporary.chmod(path.stat().st_mode & 0o777 if path.exists() else 0o600)
            os.replace(temporary, path)
        finally:
            temporary.unlink(missing_ok=True)
base = work / 'waydroid_base.prop'
refresh = changed or not base.is_file() or f'{key}=true' not in base.read_text(encoding='utf-8').splitlines()
print(int(needs_init), int(refresh))
PY
}

configure_firewall() {
    if systemctl is-active --quiet firewalld.service; then
        # Apply both states directly: reload would discard unrelated runtime rules.
        firewall-cmd --zone=trusted --change-interface=waydroid0 --permanent
        firewall-cmd --zone=trusted --change-interface=waydroid0
        log_success 'Firewalld configured for waydroid0 (runtime and permanent).'
    elif command -v ufw >/dev/null; then
        local status
        status=$(ufw status)
        if [[ "$status" == 'Status: active'$'\n'* || "$status" == 'Status: active' ]]; then
            ufw allow in on waydroid0
            ufw route allow in on waydroid0
            log_success 'UFW inbound and forwarding rules configured for waydroid0.'
        fi
    fi
    # Waydroid owns forwarding, NAT and its iptables/nftables rules on session start.
}

run_addons() {
    ask_yes "Run casualsnek's optional Waydroid add-on installer (requires internet)?" || return 0
    ensure_packages git lzip
    TEMP_DIR=$(mktemp -d)
    git clone --depth=1 https://github.com/casualsnek/waydroid_script "$TEMP_DIR/addons"
    python3 -m venv "$TEMP_DIR/venv"
    "$TEMP_DIR/venv/bin/python" -m pip install -r "$TEMP_DIR/addons/requirements.txt"
    (cd "$TEMP_DIR/addons" && "$TEMP_DIR/venv/bin/python" main.py)
}

main() {
    if [[ ${WAYDROID_SETUP_RUNNING:-} != 1 ]]; then
        log_info 'Set up Waydroid using manually downloaded Android images.'
        ask_yes 'Proceed with Waydroid installation?' || { log_info 'Skipped.'; return 0; }
    fi
    if (( EUID != 0 )); then
        exec sudo WAYDROID_SETUP_RUNNING=1 bash "$(realpath -e -- "${BASH_SOURCE[0]}")" "$@"
    fi
    trap cleanup EXIT
    trap 'exit 130' INT
    trap 'exit 143' TERM
    trap 'printf "[ERROR] Setup failed at line %s.\n" "$LINENO" >&2' ERR
    export LC_ALL=C
    umask 022
    exec {setup_lock}>/run/lock/waydroid-setup.lock
    flock --nonblock "$setup_lock" || log_error 'Another Waydroid setup is running.'

    log_info 'Checking Binder support in the running kernel...'
    if ! grep -qw binder /proc/filesystems; then
        modprobe binder_linux || log_error 'Binder unavailable. Boot a kernel with CONFIG_ANDROID_BINDER_IPC and CONFIG_ANDROID_BINDERFS, or install a matching binder_linux module.'
        grep -qw binder /proc/filesystems || log_error 'The running kernel does not expose BinderFS.'
    fi
    # Waydroid is in Arch extra. Pacman can also use its cache for offline installs.
    ensure_packages waydroid unzip
    command -v waydroid >/dev/null || log_error 'Waydroid command missing after package installation.'

    if images_ready; then
        log_info "Existing image pair found in $DEST_DIR."
        if ask_yes 'Replace both images with downloaded files?'; then
            import_images
        fi
    else
        if ! ask_yes 'Are matching system and vendor images downloaded?'; then
            log_info 'Download both images and rerun this script.'
            return 0
        fi
        import_images
    fi
    images_ready || log_error 'Both nonempty image files are required.'

    local state needs_init refresh
    state=$(configure_waydroid)
    read -r needs_init refresh <<< "$state"
    if (( needs_init )) || [[ -e "$WORK_DIR/.setup-init-pending" || -e "$DEST_DIR/.setup-init-pending" ]]; then
        systemctl stop "$SERVICE"
        : > "$WORK_DIR/.setup-init-pending"
        log_info 'Initializing Waydroid with the local image pair...'
        waydroid init --force --images_path "$DEST_DIR"
    elif (( refresh )); then
        systemctl stop "$SERVICE"
        log_info 'Refreshing configuration without downloading images or resetting overlays...'
        waydroid upgrade --offline
    else
        log_success 'Existing initialization and multi-window configuration reused.'
    fi
    # Check the resulting configuration as well as the CLI's exit status before
    # clearing recovery state or reporting success.
    state=$(configure_waydroid)
    [[ "$state" == '0 0' ]] || log_error 'Waydroid initialization or multi-window configuration is incomplete.'
    rm -f -- "$WORK_DIR/.setup-init-pending" "$DEST_DIR/.setup-init-pending"

    configure_firewall
    run_addons
    systemctl enable --now "$SERVICE"
    systemctl is-active --quiet "$SERVICE" || log_error "Management service failed. Inspect: journalctl -u $SERVICE -b"
    log_success 'Waydroid setup complete; management service active.'
    log_info 'In your Hyprland user session, run: waydroid session start'
    log_info 'Then launch an installed Android app, or run: waydroid show-full-ui'
}

if [[ ${BASH_SOURCE[0]} == "$0" ]]; then
    main "$@"
fi
