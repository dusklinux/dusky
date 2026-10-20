#!/usr/bin/env bash
#d: Play sounds when USB devices are connected or disconnected

set -euo pipefail

readonly RED=$'\033[0;31m'
readonly GREEN=$'\033[0;32m'
readonly BLUE=$'\033[0;34m'
readonly NC=$'\033[0m'

log_info()    { printf '%s[INFO]%s %s\n' "$BLUE" "$NC" "$1"; }
log_success() { printf '%s[OK]%s %s\n' "$GREEN" "$NC" "$1"; }
log_error()   { printf '%s[ERROR]%s %s\n' "$RED" "$NC" "$1" >&2; }

install_file() {
    local source=$1 target=$2 mode=$3
    local target_dir=${2%/*} staged

    # Preserve unchanged files, including their inode and timestamps.
    if [[ -f "$target" && ! -L "$target" ]] &&
       [[ $(stat -c '%a:%u:%g' -- "$target") == "$mode:0:0" ]] &&
       cmp -s -- "$source" "$target"; then
        return 0
    fi

    [[ -d "$target_dir" ]] || install -d -- "$target_dir"
    # Staging on the same filesystem makes replacement an atomic rename.
    staged=$(mktemp --tmpdir="$target_dir" .usb-sound.XXXXXXXX)
    STAGED_FILES+=("$staged")
    install -T -m "$mode" -o root -g root -- "$source" "$staged"
    mv -fT -- "$staged" "$target"
}

SCRIPT_PATH=$(realpath -- "${BASH_SOURCE[0]}")
readonly SCRIPT_PATH

if [[ $EUID -ne 0 ]]; then
    log_info "Elevating to root..."
    exec sudo /usr/bin/bash "$SCRIPT_PATH" "$@"
fi

readonly SOURCE_SCRIPT="${SCRIPT_PATH%/*}/../../external/usb_sound.sh"
readonly TARGET_BIN="/usr/local/bin/usb_sound.sh"
readonly UDEV_RULE_FILE="/etc/udev/rules.d/90-usb-sound.rules"

readonly UDEV_RULE_CONTENT='ACTION=="add", SUBSYSTEM=="usb", ENV{DEVTYPE}=="usb_device", RUN+="/usr/local/bin/usb_sound.sh connect"
ACTION=="remove", SUBSYSTEM=="usb", ENV{DEVTYPE}=="usb_device", RUN+="/usr/local/bin/usb_sound.sh disconnect"'

if [[ ! -f "$SOURCE_SCRIPT" ]]; then
    log_error "Source script not found: $SOURCE_SCRIPT"
    exit 1
fi

# Validate before modifying the installed files.
bash -n "$SOURCE_SCRIPT"
RULE_DIR=$(mktemp --directory)
readonly RULE_DIR
STAGED_FILES=()
trap 'rm -rf -- "$RULE_DIR" "${STAGED_FILES[@]}"' EXIT
printf '%s\n' "$UDEV_RULE_CONTENT" > "$RULE_DIR/90-usb-sound.rules"
udevadm verify "$RULE_DIR/90-usb-sound.rules"

# Install explicit permissions independent of the caller's umask.
log_info "Installing payload to system binaries..."
install_file "$SOURCE_SCRIPT" "$TARGET_BIN" 755
log_success "Installed to $TARGET_BIN"

# Step 2: Udev rules
log_info "Deploying udev rules..."
install_file "$RULE_DIR/90-usb-sound.rules" "$UDEV_RULE_FILE" 644
log_success "Udev rules deployed to $UDEV_RULE_FILE"

# Step 3: Daemon reload
log_info "Reloading systemd-udevd state..."
udevadm control --reload
log_success "Udev subsystem reloaded and active for future hotplug events"

log_success "Setup complete. System is configured."
