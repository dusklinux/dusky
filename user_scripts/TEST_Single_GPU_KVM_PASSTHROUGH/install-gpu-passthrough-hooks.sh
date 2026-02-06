#!/usr/bin/env bash
#
# Install GPU Passthrough Libvirt Hooks
# This script copies the hook scripts to the correct location and sets permissions
#
set -euo pipefail

# Colors
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m'

print_header() {
    printf '\n%b=== %s ===%b\n' "$BLUE" "$1" "$NC"
}

print_ok() {
    printf '%b✓%b %s\n' "$GREEN" "$NC" "$1"
}

print_error() {
    printf '%b✗%b %s\n' "$RED" "$NC" "$1"
}

# Check if running as root
if [[ $EUID -ne 0 ]]; then
    print_error "This script must be run as root (use sudo)"
    exit 1
fi

VM_NAME="${1:-win11}"

print_header "Installing GPU Passthrough Hooks for VM: $VM_NAME"

# Source directory (where we created the hooks)
SOURCE_DIR="/home/$SUDO_USER/user_scripts_local/Single_GPU_KVM_PASSTHROUGH/libvirt-hooks"

# Target directory
TARGET_DIR="/etc/libvirt/hooks"

# Check if source files exist
if [[ ! -f "$SOURCE_DIR/qemu" ]]; then
    print_error "Source hook files not found in $SOURCE_DIR"
    exit 1
fi

# Create target directories
print_ok "Creating hook directories"
mkdir -p "$TARGET_DIR/qemu.d/$VM_NAME/prepare/begin"
mkdir -p "$TARGET_DIR/qemu.d/$VM_NAME/release/end"

# Copy main dispatcher
print_ok "Installing main QEMU hook dispatcher"
cp "$SOURCE_DIR/qemu" "$TARGET_DIR/qemu"
chmod +x "$TARGET_DIR/qemu"

# Copy VM-specific hooks
print_ok "Installing VM start hook"
cp "$SOURCE_DIR/$VM_NAME/prepare/begin/start.sh" "$TARGET_DIR/qemu.d/$VM_NAME/prepare/begin/start.sh"
chmod +x "$TARGET_DIR/qemu.d/$VM_NAME/prepare/begin/start.sh"

print_ok "Installing VM stop hook"
cp "$SOURCE_DIR/$VM_NAME/release/end/stop.sh" "$TARGET_DIR/qemu.d/$VM_NAME/release/end/stop.sh"
chmod +x "$TARGET_DIR/qemu.d/$VM_NAME/release/end/stop.sh"

# Verify installation
print_header "Verifying Installation"

if [[ -x "$TARGET_DIR/qemu" ]]; then
    print_ok "Main hook is executable"
else
    print_error "Main hook is not executable"
fi

if [[ -x "$TARGET_DIR/qemu.d/$VM_NAME/prepare/begin/start.sh" ]]; then
    print_ok "Start hook is executable"
else
    print_error "Start hook is not executable"
fi

if [[ -x "$TARGET_DIR/qemu.d/$VM_NAME/release/end/stop.sh" ]]; then
    print_ok "Stop hook is executable"
else
    print_error "Stop hook is not executable"
fi

# Show directory structure
print_header "Hook Directory Structure"
tree -L 5 "$TARGET_DIR" 2>/dev/null || find "$TARGET_DIR" -type f -o -type d | sort

print_header "Configuration Check"
printf 'VM Name:        %s\n' "$VM_NAME"
printf 'GPU PCI:        0000:01:00.0\n'
printf 'Audio PCI:      0000:01:00.1\n'
printf 'Display Mgr:    sddm\n'
printf 'Safety Timeout: %s minutes (adjustable via GPU_PASSTHROUGH_TIMEOUT)\n' "${GPU_PASSTHROUGH_TIMEOUT:-5}"

print_header "Next Steps"
printf '1. Restart libvirtd: sudo systemctl restart libvirtd\n'
printf '2. Create Windows VM (if not already created)\n'
printf '3. Add GPU to VM XML: sudo virsh edit %s\n' "$VM_NAME"
printf '4. Set timeout: export GPU_PASSTHROUGH_TIMEOUT=5  # minutes\n'
printf '5. Test with: virsh start %s\n' "$VM_NAME"
printf '\n%bIMPORTANT:%b Have SSH ready from another device!\n' "$YELLOW" "$NC"
printf 'SSH: ssh %s@10.10.10.9\n' "$SUDO_USER"

printf '\n%b✓ Installation complete!%b\n\n' "$GREEN" "$NC"
