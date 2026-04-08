#!/usr/bin/env bash
# -----------------------------------------------------------------------------
# Elite Arch Linux Setup :: Config Propagation
# Context: Live ISO Environment (Pre-Chroot)
# -----------------------------------------------------------------------------

# --- 1. Strict Mode ---
# -e: Exit on error
# -u: Exit on unset variables
# -o pipefail: Exit if any command in a pipe fails
set -euo pipefail

# --- 2. Configuration ---
readonly MNT_POINT="/mnt"
readonly SRC_DIR="dusky"
readonly POST_CHROOT_SRC="${SRC_DIR}/user_scripts/arch_iso_scripts/001_post_chroot"

# --- 3. Logging (TTY Aware) ---
if [[ -t 1 ]]; then
    _log()     { printf "\033[1;34m[INFO]\033[0m %s\n" "$*"; }
    _success() { printf "\033[1;32m[OK]\033[0m %s\n" "$*"; }
    _error()   { printf "\033[1;31m[ERROR]\033[0m %s\n" "$*" >&2; exit 1; }
else
    _log()     { echo "[INFO] $*"; }
    _success() { echo "[OK] $*"; }
    _error()   { echo "[ERROR] $*" >&2; exit 1; }
fi

# --- 4. Pre-Flight Validation ---

# Check 1: Root Privileges (EUID 0)
(( EUID == 0 )) || _error "This script must be run as root."

# Check 2: Mountpoint Safety
# We strictly ensure /mnt is a mountpoint to prevent writing to RAM.
mountpoint -q "${MNT_POINT}" || _error "'${MNT_POINT}' is not a mounted filesystem. Please mount your partitions first."

# Check 3: Working Directory Verification
# We ensure the script is run from the location containing 'dusky' to satisfy relative paths.
[[ -d "${SRC_DIR}" ]] || _error "Directory '${SRC_DIR}' not found in $(pwd). Please run this script from the repository root."

# Check 4: Source Payload Verification
[[ -d "${POST_CHROOT_SRC}" ]] || _error "Post-chroot scripts not found at: ${POST_CHROOT_SRC}"

# --- 5. Execution ---

# TASK 1: Inject Post-Chroot Scripts
# We use trailing '/.' to copy contents (including hidden files) cleanly.
# -R: Recursive
# -f: Force (overwrite existing)
# -p: Preserve attributes (timestamps, ownership, PERMISSIONS) -> Critical
_log "Propagating post-chroot scripts to ${MNT_POINT}..."
cp -Rfp -- "${POST_CHROOT_SRC}/." "${MNT_POINT}/"
_success "Post-chroot scripts injected."

# TASK 2: Copy the 'dusky' Environment
# This creates /mnt/dusky
_log "Copying '${SRC_DIR}' repository to ${MNT_POINT}..."
cp -Rfp -- "${SRC_DIR}" "${MNT_POINT}/"
_success "Environment '${SRC_DIR}' copied successfully."

# --- 6. Completion ---
_success "File propagation complete. Ready for 'arch-chroot /mnt'."
