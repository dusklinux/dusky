#!/usr/bin/env bash
# ==============================================================================
# BTRFS ENOSPC Universal Rescue Automation
# ==============================================================================

set -euo pipefail

# --- Formatting ---
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
CYAN='\033[0;36m'
NC='\033[0m' # No Color

# --- Auto-Elevation to Root ---
if [[ $EUID -ne 0 ]]; then
    echo -e "${CYAN}[INFO]${NC} Elevating privileges to root..."
    exec sudo "$0" "$@"
fi

# --- Global Variables & State Tracking ---
TARGET_MOUNT=""
IMAGE_SIZE_GB=2
RESCUE_IMG=""
LOOP_DEV=""
BTRFS_ATTACHED=0
LOG_FILE="/var/log/btrfs-enospc-rescue.log"

log_info()    { echo -e "${CYAN}[INFO]${NC} $1"     | tee -a "$LOG_FILE" 2>/dev/null || true; }
log_success() { echo -e "${GREEN}[SUCCESS]${NC} $1"  | tee -a "$LOG_FILE" 2>/dev/null || true; }
log_warn()    { echo -e "${YELLOW}[WARNING]${NC} $1" | tee -a "$LOG_FILE" 2>/dev/null || true; }
log_error()   { echo -e "${RED}[FATAL]${NC} $1"     | tee -a "$LOG_FILE" 2>/dev/null || true; }

# --- Crash & Cleanup Trap ---
cleanup() {
    local exit_code=$?
    set +e # Disable exit-on-error to ensure all cleanup steps run

    echo "================================================================"
    log_info "Running cleanup routines..."

    if [[ "$BTRFS_ATTACHED" -eq 1 ]]; then
        log_error "SCRIPT ABORTED WHILE LOOP DEVICE IS STILL ATTACHED TO BTRFS!"
        log_error "DO NOT REBOOT. DO NOT DELETE $RESCUE_IMG."
        log_error "To safely recover, manually run: btrfs device remove $LOOP_DEV $TARGET_MOUNT"
        exit "$exit_code"
    fi

    if [[ -n "$LOOP_DEV" ]] && losetup "$LOOP_DEV" >/dev/null 2>&1; then
        losetup -d "$LOOP_DEV"
        log_info "Loop device $LOOP_DEV detached."
    fi

    if [[ -n "$RESCUE_IMG" ]] && [[ -f "$RESCUE_IMG" ]]; then
        rm -f "$RESCUE_IMG"
        log_info "Temporary rescue image removed."
    fi

    if [[ $exit_code -eq 0 ]]; then
        log_success "Operation completed smoothly."
    else
        log_error "Operation failed with exit code $exit_code."
    fi
    exit "$exit_code"
}

trap cleanup EXIT INT TERM

echo "================================================================"
log_info "Initializing BTRFS ENOSPC Rescue..."

# --- Phase 1: Interactive Target Selection ---
if [[ -z "${1:-}" ]]; then
    log_info "No target specified. Scanning for BTRFS mounts..."
    # -l forces list format, stripping out tree drawing characters (├─)
    mapfile -t btrfs_mounts < <(findmnt -l -t btrfs -no TARGET)
    
    if [[ ${#btrfs_mounts[@]} -eq 0 ]]; then
        log_error "No BTRFS filesystems found!"
        exit 1
    fi
    
    echo -e "\n${CYAN}Available BTRFS drives:${NC}"
    for i in "${!btrfs_mounts[@]}"; do
        usage_info=$(df -h "${btrfs_mounts[$i]}" | awk 'NR==2 {print "Size: "$2", Used: "$5}')
        echo -e "  ${YELLOW}$((i+1)))${NC} ${btrfs_mounts[$i]}  ($usage_info)"
    done
    echo ""
    
    read -r -p "Enter the number of the drive to rescue: " choice </dev/tty
    
    if ! [[ "$choice" =~ ^[0-9]+$ ]] || (( choice < 1 || choice > ${#btrfs_mounts[@]} )); then
        log_error "Invalid selection."
        exit 1
    fi
    TARGET_MOUNT="${btrfs_mounts[$((choice-1))]}"
else
    TARGET_MOUNT="$1"
fi

log_info "Target selected: $TARGET_MOUNT"

# --- Phase 2: Pre-flight Validations ---
for cmd in btrfs losetup fallocate findmnt df awk mktemp dd; do
    if ! command -v "$cmd" >/dev/null 2>&1; then
        log_error "Required dependency missing: $cmd"
        exit 1
    fi
done

if ! findmnt -l -t btrfs "$TARGET_MOUNT" >/dev/null 2>&1; then
    log_error "Path '$TARGET_MOUNT' is either not mounted or is NOT a BTRFS filesystem."
    exit 1
fi

if findmnt -no OPTIONS "$TARGET_MOUNT" | grep -qE '(^|,)ro(,|$)'; then
    log_error "Filesystem at '$TARGET_MOUNT' is mounted read-only."
    log_error "Remount it read-write first:  mount -o remount,rw $TARGET_MOUNT"
    exit 1
fi

# --- Phase 3: Dynamic Workspace Discovery ---
log_info "Searching for optimal physical volume to host the ${IMAGE_SIZE_GB}GB rescue image..."

# Identify the underlying block device of the target to prevent subvolume collisions
TARGET_FS=$(df -P "$TARGET_MOUNT" | awk 'NR==2 {print $1}')

# Exclude target device, zram, and legacy/network/virtual filesystems
WORKSPACE_MOUNT=$(df -PT -B 1G | awk -v target_fs="$TARGET_FS" '
    NR>1 &&
    $5 >= 3 &&
    $1 != target_fs &&
    $7 !~ "^/(dev|proc|sys|run)" &&
    $1 !~ "^/dev/zram" &&
    $2 !~ "^(tmpfs|devtmpfs|squashfs|overlay|nfs|nfs4|cifs|smbfs|fuse\\.|ext2|ext3|vfat|fat)" {
        print $7, $5
    }
' | sort -k2 -n -r | head -n 1 | awk '{print $1}')

if [[ -z "$WORKSPACE_MOUNT" ]]; then
    log_error "Could not find any suitable physical filesystem with at least 3GB of free space."
    exit 1
fi

log_success "Workspace selected: $WORKSPACE_MOUNT"

# --- Phase 4: Provision Loop Device ---
log_info "Pre-allocating ${IMAGE_SIZE_GB}GB rescue block device in $WORKSPACE_MOUNT..."

RESCUE_IMG=$(mktemp "${WORKSPACE_MOUNT}/btrfs-rescue-XXXXXX.img") || {
    log_error "mktemp failed. Check permissions and available inodes on $WORKSPACE_MOUNT."
    exit 1
}

if ! fallocate -l "${IMAGE_SIZE_GB}G" "$RESCUE_IMG" 2>/dev/null; then
    log_warn "fallocate not supported on $WORKSPACE_MOUNT, falling back to safe 'dd' write."
    log_info "Writing ${IMAGE_SIZE_GB}GB zero-blocks. This will take a few seconds..."
    dd if=/dev/zero of="$RESCUE_IMG" bs=1M count=$((IMAGE_SIZE_GB * 1024)) status=none || {
        log_error "dd allocation failed. The workspace may have physically filled up."
        exit 1
    }
fi
log_info "Rescue image created at $RESCUE_IMG"

log_info "Mapping to loopback interface..."
LOOP_DEV=$(losetup -f --show "$RESCUE_IMG") || {
    log_error "losetup failed. Check that loop devices are available (/dev/loop*)."
    exit 1
}
log_success "Mapped to $LOOP_DEV"

# --- Phase 5: BTRFS Expansion ---
log_info "Injecting $LOOP_DEV into the BTRFS pool at $TARGET_MOUNT..."
btrfs device add -f "$LOOP_DEV" "$TARGET_MOUNT"
BTRFS_ATTACHED=1 # STATE CHANGE
log_success "Device injected. Metadata allocator now has raw breathing room."

# --- Phase 6: Iterative Packing (Balance) ---
log_info "Beginning Iterative Data Packing (Balancing)..."
for usage in 5 10 20 30 50; do
    log_info "Balancing data chunks with usage < ${usage}%..."
    if btrfs balance start -dusage="$usage" "$TARGET_MOUNT" >/dev/null 2>&1; then
        log_success "Data balance pass ${usage}% completed."
    else
        log_warn "Data balance pass ${usage}% hit a minor warning (normal). Continuing."
    fi
    sync
done

log_info "Consolidating metadata chunks (musage=50)..."
if btrfs balance start -musage=50 "$TARGET_MOUNT" >/dev/null 2>&1; then
    log_success "Metadata balance completed."
else
    log_warn "Metadata balance hit a minor warning. Continuing."
fi
sync

# --- Phase 7: Safe Evacuation (Interactive Retry Loop) ---
log_info "Evacuating BTRFS data from the rescue loop device..."
log_info "This requires BTRFS to push all data back to your primary physical drive."

while true; do
    if btrfs device remove "$LOOP_DEV" "$TARGET_MOUNT"; then
        BTRFS_ATTACHED=0 # STATE CHANGE
        log_success "Rescue device successfully evacuated and removed from the BTRFS pool."
        break # Exit the infinite loop on success
    else
        log_error "Evacuation failed! The primary drive is physically too full to receive the packed data."
        
        echo -e "\n${RED}================================================================${NC}"
        echo -e "${YELLOW}ACTION REQUIRED:${NC} You MUST manually free up space on ${TARGET_MOUNT}"
        echo -e "  1. Open another terminal window."
        echo -e "  2. Delete old files, clear browser caches, or empty trash on ${TARGET_MOUNT}."
        echo -e "  3. Return to this window and press Enter to retry the evacuation."
        echo -e "\n${RED}WARNING: DO NOT REBOOT.${NC} Your filesystem is spanning across the temporary image."
        echo -e "${RED}================================================================${NC}\n"
        
        read -r -p "Press [Enter] to retry evacuation, or type 'abort' to exit: " user_choice </dev/tty
        
        if [[ "${user_choice,,}" == "abort" ]]; then
            log_error "User initiated manual abort."
            exit 1 # The trap function will catch this and print the manual recovery commands
        fi
        
        log_info "Retrying evacuation..."
    fi
done

# --- Final Report ---
log_success "Rescue complete. Post-rescue filesystem usage:"
btrfs filesystem usage "$TARGET_MOUNT" 2>/dev/null || true
