#!/usr/bin/env bash
# Configures Preload, a caching service for linux binaries to make system faster
# -----------------------------------------------------------------------------
# Script: 059_preload_config.sh
# Description: Configures /etc/preload.conf optimized for High-RAM systems (32GB+).
#              Includes backup logic, auto-install, service state management,
#              and "human-readable" pacing.
# Author: Elite DevOps (Arch/Hyprland)
# Dependencies: preload, systemd, bash 5+, paru OR yay
# -----------------------------------------------------------------------------

# 1. Strict Safety & Error Handling
set -euo pipefail

# 2. Configuration Content
# This configuration is tuned for aggressive caching on systems with ample RAM.
mapfile -d '' PRELOAD_CONFIG_CONTENT <<'EOF'
[model]

# cycle:
#
# This is the quantum of time for preload.  Preload performs
# data gathering and predictions every cycle.  Use an even
# number.
#
# Note: Setting this parameter too low may reduce system performance
# and stability.
#
# unit: seconds
# default: 20
#
cycle = 10

# usecorrelation:
#
# Whether correlation coefficient should be used in the prediction
# algorithm.  There are arguments both for and against using it.
# Currently it's believed that using it results in more accurate
# prediction.  The option may be removed in the future.
#
# default: true
usecorrelation = true

# minsize:
#
# Minimum sum of the length of maps of the process for
# preload to consider tracking the application.
#
# Note: Setting this parameter too high will make preload less
# effective, while setting it too low will make it eat
# quadratically more resources, as it tracks more processes.
#
# unit: bytes
# default: 2000000
#
minsize = 500000

#
# The following control how much memory preload is allowed to use
# for preloading in each cycle.  All values are percentages and are
# clamped to -100 to 100.
#
# The total memory preload uses for prefetching is then computed using
# the following formulae:
#
# 	max (0, TOTAL * memtotal + FREE * memfree) + CACHED * memcached
# where TOTAL, FREE, and CACHED are the respective values read at
# runtime from /proc/meminfo.
#

# memtotal: precentage of total memory
#
# unit: signed_integer_percent
# default: -10
#
memtotal = 50

# memfree: precentage of free memory
#
# unit: signed_integer_percent
# default: 50
#
memfree = 95

# memcached: precentage of cached memory
#
# unit: signed_integer_percent
# default: 0
#
memcached = 10


###########################################################################

[system]

# doscan:
#
# Whether preload should monitor running processes and update its
# model state.  Normally you do want that, that's all preload is
# about, but you may want to temporarily turn it off for various
# reasons like testing and only make predictions.  Note that if
# scanning is off, predictions are made based on whatever processes
# have been running when preload started and the list of running
# processes is not updated at all.
#
# default: true
doscan = true

# dopredict:
#
# Whether preload should make prediction and prefetch anything off
# the disk.  Quite like doscan, you normally want that, that's the
# other half of what preload is about, but you may want to temporarily
# turn it off, to only train the model for example.  Note that
# this allows you to turn scan/predict or or off on the fly, by
# modifying the config file and signalling the daemon.
#
# default: true
dopredict = true

# autosave:
#
# Preload will automatically save the state to disk every
# autosave period.  This is only relevant if doscan is set to true.
# Note that some janitory work on the model, like removing entries
# for files that no longer exist happen at state save time.  So,
# turning off autosave completely is not advised.
#
# unit: seconds
# default: 3600
#
autosave = 3600

# mapprefix:
#
# A list of path prefixes that controll which mapped file are to
# be considered by preload and which not.  The list items are
# separated by semicolons.  Matching will be stopped as soon as
# the first item is matched.  For each item, if item appears at
# the beginning of the path of the file, then a match occurs, and
# the file is accepted.  If on the other hand, the item has a
# exclamation mark as its first character, then the rest of the
# item is considered, and if a match happens, the file is rejected.
# For example a value of !/lib/modules;/ means that every file other
# than those in /lib/modules should be accepted.  In this case, the
# trailing item can be removed, since if no match occurs, the file is
# accepted.  It's advised to make sure /dev is rejected, since
# preload doesn't special-handle device files internally.
#
# Note that /lib matches all of /lib, /lib64, and even /libexec if
# there was one.  If one really meant /lib only, they should use
# /lib/ instead.
#
# default: (empty list, accept all)
# Added /usr/local/ for self-compiled tools common in Arch
mapprefix = /opt;/usr/;/lib;/var/cache/;!/
# exeprefix:
#
# The syntax for this is exactly the same as for mapprefix.  The only
# difference is that this is used to accept or reject binary exectuable
# files instead of maps.
#
# default: (empty list, accept all)
exeprefix = /opt;!/usr/sbin/;!/usr/local/sbin/;!/usr/local/;/usr/;/lib;!/
# maxprocs
#
# Maximum number of processes to use to do parallel readahead.  If
# equal to 0, no parallel processing is done and all readahead is
# done in-process.  Parallel readahead supposedly gives a better I/O
# performance as it allows the kernel to batch several I/O requests
# of nearby blocks.
#
# default: 30
processes = 60

# sortstrategy
#
# The I/O sorting strategy.  Ideally this should be automatically
# decided, but it's not currently.  One of:
#
#   0 -- SORT_NONE:	No I/O sorting.
#			Useful on Flash memory for example.
#   1 -- SORT_PATH:	Sort based on file path only.
#			Useful for network filesystems.
#   2 -- SORT_INODE:	Sort based on inode number.
#			Does less house-keeping I/O than the next option.
#   3 -- SORT_BLOCK:	Sort I/O based on disk block.  Most sophisticated.
#			And useful for most Linux filesystems.
#
# default: 3
sortstrategy = 0
EOF

# 3. Aesthetics & Logging
readonly C_RESET=$'\033[0m'
readonly C_GREEN=$'\033[1;32m'
readonly C_BLUE=$'\033[1;34m'
readonly C_RED=$'\033[1;31m'
readonly C_YELLOW=$'\033[1;33m'

log_info() { printf "${C_BLUE}[INFO]${C_RESET} %s\n" "$1"; }
log_success() { printf "${C_GREEN}[OK]${C_RESET} %s\n" "$1"; }
log_warn() { printf "${C_YELLOW}[WARN]${C_RESET} %s\n" "$1"; }
log_error() { printf "${C_RED}[ERROR]${C_RESET} %s\n" "$1" >&2; }

# 4. Root Privilege Check
if [[ $EUID -ne 0 ]]; then
    log_info "Root privileges required. Elevating..."
    exec sudo "$0" "$@"
fi

# 5. Environment Validation & Auto-Install
if command -v preload &>/dev/null; then
    log_info "Preload is already installed."
else
    log_warn "Preload is not installed."
    
    # Detect the real user (Orchestra runs as root, so we need SUDO_USER)
    real_user="${SUDO_USER:-$USER}"
    
    if [[ "$real_user" == "root" ]]; then
        log_error "Cannot determine non-root user for AUR install. Install 'preload' manually."
        exit 1
    fi

    # Determine AUR helper (Paru > Yay)
    # FIX: We use 'bash -c' because 'command' is a shell builtin, and sudo cannot execute builtins directly.
    aur_helper=""
    if sudo -u "$real_user" bash -c "command -v paru" &>/dev/null; then
        aur_helper="paru"
    elif sudo -u "$real_user" bash -c "command -v yay" &>/dev/null; then
        aur_helper="yay"
    else
        log_error "Neither 'paru' nor 'yay' found. Please install 'preload' manually."
        exit 1
    fi

    log_info "Installing preload using ${aur_helper} as user: ${real_user}..."
    if sudo -u "$real_user" "$aur_helper" -S --needed --noconfirm preload; then
        log_success "Preload installed successfully."
        sleep 0.5
    else
        log_error "Failed to install preload."
        exit 1
    fi
fi

# 6. Main Execution
main() {
    local target_file="/etc/preload.conf"
    
    # ---------------------------------------------------------
    # A. User Interaction & Warnings
    # ---------------------------------------------------------
    echo ""
    log_warn "You are about to apply a Preload configuration tuned specifically for:"
    log_warn "High-Performance Systems (32GB+ RAM)"
    echo ""
    printf "  %bOptimization Note:%b This config is optimized for computers with 32GB of RAM and above.\n" "${C_GREEN}" "${C_RESET}"
    printf "  You could also apply it on 16GB systems, but it is not explicitly recommended.\n"
    printf "  %bPerformance Trade-off:%b This will cache shared libraries and binaries in RAM\n" "${C_RED}" "${C_RESET}"
    printf "  to significantly speed up system responsiveness. This comes at the cost of\n"
    printf "  increased RAM usage. If you want high RAM usage at the benefit of snappiness,\n"
    printf "  please proceed.\n"
    printf "  You can run this script now, and further tweak the settings later to your liking at:\n"
    printf "  %bsudo nvim /etc/preload.conf%b\n\n" "${C_BLUE}" "${C_RESET}"

    # Note: If running Orchestra in "Autonomous Mode", this prompt might be skipped or handled differently by the caller,
    # but since this script handles its own 'read', it will pause unless piped 'yes'.
    read -r -p "Do you want to proceed with applying this configuration? [y/N] " response
    if [[ ! "$response" =~ ^[yY]$ ]]; then
        log_info "Operation cancelled by user."
        exit 0
    fi

    # ---------------------------------------------------------
    # B. Backup Logic
    # ---------------------------------------------------------
    local real_user="${SUDO_USER:-$USER}"
    local real_home
    real_home=$(getent passwd "$real_user" | cut -d: -f6)
    
    local backup_dir="${real_home}/Documents"
    local backup_file="${backup_dir}/preload_backup.conf"
    local file_existed=false

    if [[ -f "$target_file" ]]; then
        file_existed=true
        if [[ ! -d "$backup_dir" ]]; then
            log_info "Creating directory ${backup_dir}..."
            mkdir -p "$backup_dir"
            chown "$real_user:$(id -gn "$real_user")" "$backup_dir"
        fi

        log_info "Backing up current config to ${backup_file}..."
        cp "$target_file" "$backup_file"
        chown "$real_user:$(id -gn "$real_user")" "$backup_file"
        log_success "Backup verified."
        sleep 0.5 
    fi

    # ---------------------------------------------------------
    # C. Write Configuration
    # ---------------------------------------------------------
    if [[ "$file_existed" == "true" ]]; then
        log_info "Overwriting existing file at ${target_file}..."
    else
        log_info "File did not exist. Creating new file at ${target_file}..."
    fi
    
    if printf "%s" "${PRELOAD_CONFIG_CONTENT}" > "${target_file}"; then
        log_success "Configuration written successfully."
        sleep 0.5
    else
        log_error "Failed to write to ${target_file}."
        exit 1
    fi

    # ---------------------------------------------------------
    # D. Reload Service
    # ---------------------------------------------------------
    log_info "Managing Preload systemd service..."

    if systemctl is-enabled preload.service &>/dev/null; then
        log_info "Preload service is already enabled."
    else
        log_info "Enabling Preload service..."
        if systemctl enable --now preload.service; then
            log_success "Preload service enabled and started."
            sleep 0.5
        else
            log_error "Failed to enable Preload service."
            exit 1
        fi
    fi

    # Restart to apply new config
    if systemctl restart preload.service; then
        log_success "Preload configuration applied (Service Restarted)."
    else
        log_warn "Service restart failed. Please check 'systemctl status preload'."
    fi
}

# Run Main
main
