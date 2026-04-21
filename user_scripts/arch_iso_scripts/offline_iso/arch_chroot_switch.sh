#!/usr/bin/env bash
#
# Arch Linux Interactive Chroot Switcher
# Drops the user into the chroot shell.
#

set -Eeuo pipefail

readonly TARGET_MOUNT="/mnt"
readonly RED=$'\e[31m'
readonly GREEN=$'\e[32m'
readonly RESET=$'\e[0m'

# Pre-flight: Ensure the target is actually mounted before switching
if ! mountpoint -q "$TARGET_MOUNT"; then
    printf "${RED}[ERROR]${RESET} %s is not an active mountpoint. Did you forget to mount your partitions?\n" "$TARGET_MOUNT" >&2
    exit 1
fi

printf "${GREEN}[SUCCESS]${RESET} Transitioning interactive terminal to chroot environment at %s...\n" "$TARGET_MOUNT"

# Use 'exec' to replace the script's process with the arch-chroot process
exec arch-chroot "$TARGET_MOUNT"
