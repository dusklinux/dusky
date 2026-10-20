#!/usr/bin/env bash
#d: Configure Dusky Player (offline, system fonts)

set -euo pipefail
shopt -s globstar nullglob

SCRIPT_PATH=$(readlink -f -- "${BASH_SOURCE[0]}")
SETUP_DIR=$(cd -- "${SCRIPT_PATH%/*}/../../mpv/setup" && pwd)
readonly SETUP_DIR
readonly ASSETS_DIR="$SETUP_DIR/assets"
readonly MPV_CONFIG_DIR="${XDG_CONFIG_HOME:-$HOME/.config}/mpv"
readonly DEPENDENCIES=(mpv yt-dlp mpv-mpris ffmpeg)
BACKUP_ID=$(date +%Y%m%d_%H%M%S_%N)
readonly BACKUP_ID

log_info() { printf '[INFO] %s\n' "$1"; }
log_warn() { printf '[WARN] %s\n' "$1" >&2; }
die() { printf '[ERROR] %s\n' "$1" >&2; exit 1; }

TEMP_FILE=''
cleanup() {
    if [[ -n "$TEMP_FILE" ]]; then
        rm -f -- "$TEMP_FILE"
    fi
}
trap cleanup EXIT
trap 'exit 130' INT
trap 'exit 143' TERM

# Replace changed files atomically on the destination filesystem. Backups stay
# outside scripts/ so mpv cannot accidentally load a backed-up Lua script.
install_file() {
    local source=$1 relative=$2
    local target="$MPV_CONFIG_DIR/$relative" backup

    if [[ ! -L "$target" ]] && cmp -s -- "$source" "$target"; then
        # Restore executable permission if it was lost on an existing helper.
        if [[ -x "$source" && ! -x "$target" ]]; then
            chmod --reference="$source" -- "$target"
        fi
        return
    fi

    mkdir -p -- "${target%/*}"
    TEMP_FILE=$(mktemp "${target%/*}/.mpv-setup.XXXXXXXX")
    cp -- "$source" "$TEMP_FILE"
    chmod --reference="$source" -- "$TEMP_FILE"

    if [[ -e "$target" || -L "$target" ]]; then
        backup="$MPV_CONFIG_DIR/setup-backups/$BACKUP_ID/$relative"
        mkdir -p -- "${backup%/*}"
        cp -a -- "$target" "$backup"
        log_warn "Backed up $relative to setup-backups/$BACKUP_ID/$relative"
    fi

    mv -fT -- "$TEMP_FILE" "$target"
    TEMP_FILE=''
    log_info "Installed $relative"
}

log_info 'Starting Dusky Player setup.'

# Detect an incomplete ISO bundle before changing the installed configuration.
for relative in scripts/dusky_player/main.lua scripts/dusky_player/lib/icons.lua scripts/dusky_player/lib/waveform.lua scripts/dusky_thumbnails.lua scripts/dusky_silence.lua; do
    [[ -s "$ASSETS_DIR/$relative" ]] || die "Missing bundled asset: $relative"
done
for relative in mpv.conf input.conf script-opts/dusky_player.conf script-opts/dusky_silence.conf; do
    [[ -s "$SETUP_DIR/config/$relative" ]] || die "Missing configuration: $relative"
done

MISSING_PKGS=()
for pkg in "${DEPENDENCIES[@]}"; do
    if ! pacman -Q "$pkg" &>/dev/null; then
        MISSING_PKGS+=("$pkg")
    fi
done
if (( ${#MISSING_PKGS[@]} )); then
    log_info "Installing missing packages: ${MISSING_PKGS[*]}"
    # pacman can use its local cache offline; a failed install must report failure.
    if (( EUID == 0 )); then
        pacman -S --needed --noconfirm "${MISSING_PKGS[@]}" || die 'Package installation failed.'
    else
        sudo pacman -S --needed --noconfirm "${MISSING_PKGS[@]}" || die 'Package installation failed.'
    fi
fi

for source in "$ASSETS_DIR"/**; do
    [[ -f "$source" ]] || continue
    install_file "$source" "${source#"$ASSETS_DIR"/}"
done

# Preserve thumbnail settings when migrating the previous worker.
if [[ -f "$MPV_CONFIG_DIR/script-opts/thumbfast.conf" &&
    ! -e "$MPV_CONFIG_DIR/script-opts/dusky_thumbnails.conf" &&
    ! -L "$MPV_CONFIG_DIR/script-opts/dusky_thumbnails.conf" ]]; then
    install_file "$MPV_CONFIG_DIR/script-opts/thumbfast.conf" script-opts/dusky_thumbnails.conf
fi

# Retire the previous UI and fonts to avoid loading two controllers. Preserve
# everything in a dated backup, including user modifications to the old scripts.
for relative in scripts/uosc scripts/uosc.lua scripts/uosc_shared scripts/thumbfast.lua scripts/thumbfast_repo \
    fonts/uosc_icons.otf fonts/uosc_textures.ttf script-opts/uosc.conf script-opts/thumbfast.conf; do
    target="$MPV_CONFIG_DIR/$relative"
    if [[ -e "$target" || -L "$target" ]]; then
        backup="$MPV_CONFIG_DIR/setup-backups/$BACKUP_ID/$relative"
        mkdir -p -- "${backup%/*}"
        mv -T -- "$target" "$backup"
        log_info "Retired $relative to setup-backups/$BACKUP_ID/$relative"
    fi
done

install_file "$SETUP_DIR/config/mpv.conf" mpv.conf
install_file "$SETUP_DIR/config/input.conf" input.conf
# Preserve existing player and silence-trimming customization.
for relative in script-opts/dusky_player.conf script-opts/dusky_silence.conf; do
    if [[ ! -e "$MPV_CONFIG_DIR/$relative" && ! -L "$MPV_CONFIG_DIR/$relative" ]]; then
        install_file "$SETUP_DIR/config/$relative" "$relative"
    else
        log_info "Preserved existing $relative."
    fi
done

log_info "Dusky Player setup complete: $MPV_CONFIG_DIR"
