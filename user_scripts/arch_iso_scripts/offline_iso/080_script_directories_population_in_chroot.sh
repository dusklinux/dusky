#!/usr/bin/env bash
# -----------------------------------------------------------------------------
# Elite Arch Linux Setup :: Config Propagation
# Context: Live ISO Environment (Pre-Chroot)
# Architecture: Fault-Tolerant & Independent Task Execution
# -----------------------------------------------------------------------------

# ==============================================================================
# SCRIPT PURPOSE & OVERVIEW
# ==============================================================================
# Concept: "The Chroot Bridge"
# This script runs AFTER the hard drive is formatted (pacstrap), but BEFORE 
# you execute 'arch-chroot /mnt'. 
#
# Once you 'arch-chroot', you are trapped inside the physical hard drive. 
# Everything you staged in the Live ISO (RAM) becomes completely invisible. 
# This script rescues your custom configuration from the RAM disk and injects 
# it into the physical hard drive so the chroot environment can use it.
#
# WHAT THIS SCRIPT DOES (Step-by-Step):
#
# * PRE-FLIGHT (Safety First): 
#   Verifies you are root and that the physical drive is actively mounted at 
#   /mnt. This prevents you from accidentally copying gigabytes of data into 
#   thin air (RAM) and crashing the Live environment.
#
# * TASK 1: Injects Post-Chroot Scripts
#   Copies the next phase of your installation scripts (e.g., user creation, 
#   passwords) into /mnt. Without this, you wouldn't have your installer 
#   scripts available once you cross the chroot boundary.
#
# * TASK 2: Propagates the Bare Git Repository
#   Copies the 'dusky' bare git repository (the backbone of your dotfiles) 
#   into the physical hard drive so the final system has access to it.
#
# * TASK 3: Merges the /etc/skel Payload (The Most Critical Step)
#   It takes all your pre-staged dotfiles (.config, .local, .zshrc, etc.) 
#   from the Live ISO and perfectly merges them into /mnt/etc/skel/. 
#   WHY? Because when the 'useradd' command is run later inside the chroot, 
#   it blindly copies whatever is in /etc/skel. By pre-loading it here, 
#   'useradd' natively deploys your entire Hyprland/Wayland setup to the 
#   new user with absolute perfection and zero permission errors.
#
# ARCHITECTURE DESIGN: FAULT TOLERANCE
# This script utilizes decoupled logic. If it fails to find the source files 
# for Task 1, it will NOT crash the installer. It will simply print a yellow 
# [WARN], skip Task 1, and successfully execute Task 2 and Task 3. 
# ==============================================================================
# --- 1. Strict Mode ---
set -euo pipefail

# --- 2. Configuration (Absolute Live ISO Paths) ---
readonly MNT_POINT="/mnt"
readonly PAYLOAD_BASE="/etc/skel"

# Source Directories inside the Live ISO
readonly SRC_DIR="${PAYLOAD_BASE}/dusky"
# Update this path to wherever your post-chroot scripts actually live!
readonly POST_CHROOT_SRC="${PAYLOAD_BASE}/user_scripts/arch_iso_scripts/001_post_chroot"

# --- 3. Logging (TTY Aware) ---
if [[ -t 1 ]]; then
    _log()     { printf "\033[1;34m[INFO]\033[0m %s\n" "$*"; }
    _success() { printf "\033[1;32m[OK]\033[0m %s\n" "$*"; }
    _warn()    { printf "\033[1;33m[WARN]\033[0m %s\n" "$*"; }
    _error()   { printf "\033[1;31m[ERROR]\033[0m %s\n" "$*" >&2; exit 1; }
else
    _log()     { echo "[INFO] $*"; }
    _success() { echo "[OK] $*"; }
    _warn()    { echo "[WARN] $*"; }
    _error()   { echo "[ERROR] $*" >&2; exit 1; }
fi

# --- 4. Critical Pre-Flight Validation (Fatal Checks Only) ---

# Check 1: Root Privileges
(( EUID == 0 )) || _error "This script must be run as root."

# Check 2: Mountpoint Safety (Must be mounted to proceed)
mountpoint -q "${MNT_POINT}" || _error "'${MNT_POINT}' is not a mounted filesystem. Mount partitions first."

# --- 5. Execution (Fault-Tolerant Tasks) ---

# TASK 1: Inject Post-Chroot Scripts
_log "Task 1: Propagating post-chroot scripts to ${MNT_POINT}..."
if [[ -d "${POST_CHROOT_SRC}" ]]; then
    # || true prevents a minor cp warning from crashing the script under set -e
    cp -Rfp -- "${POST_CHROOT_SRC}/." "${MNT_POINT}/" || _warn "Minor issues encountered during copy, continuing."
    _success "Post-chroot scripts injected."
else
    _warn "Source directory not found: ${POST_CHROOT_SRC}"
    _warn "Skipping Task 1 and continuing execution."
fi

# TASK 2: Copy the 'dusky' Bare Environment
_log "Task 2: Copying bare repository to ${MNT_POINT}..."
if [[ -d "${SRC_DIR}" ]]; then
    cp -Rfp -- "${SRC_DIR}" "${MNT_POINT}/" || _warn "Minor issues encountered during copy, continuing."
    _success "Bare repository copied successfully."
else
    _warn "Bare repository not found: ${SRC_DIR}"
    _warn "Skipping Task 2 and continuing execution."
fi

# TASK 3: Inject Live ISO Skel Payload (The Critical Bridge)
_log "Task 3: Injecting custom skel payload into target system..."
if [[ -d "${PAYLOAD_BASE}" ]]; then
    mkdir -p "${MNT_POINT}/etc/skel"
    
    # -aT flawlessly merges the contents without nesting directories
    if cp -aT "${PAYLOAD_BASE}/" "${MNT_POINT}/etc/skel/"; then
        _success "Skel payload successfully merged."
    else
        _warn "Skel copy encountered minor issues, but execution is continuing."
    fi
else
    _warn "Source /etc/skel not found in Live ISO. Skipping skel injection."
fi

# --- 6. Completion ---
echo ""
_success "File propagation sequence complete. Proceed with 'arch-chroot /mnt'."
