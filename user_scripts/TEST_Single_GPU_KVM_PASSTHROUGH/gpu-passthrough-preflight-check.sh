#!/usr/bin/env bash
#
# GPU Passthrough Pre-Flight Check
# Verifies system is ready for single GPU passthrough testing
#
set -euo pipefail

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

print_header() {
    printf '\n%b=== %s ===%b\n' "$BLUE" "$1" "$NC"
}

print_ok() {
    printf '%b✓%b %s\n' "$GREEN" "$NC" "$1"
}

print_warn() {
    printf '%b⚠%b %s\n' "$YELLOW" "$NC" "$1"
}

print_error() {
    printf '%b✗%b %s\n' "$RED" "$NC" "$1"
}

# Track overall status
ERRORS=0
WARNINGS=0

print_header "System Information"
printf 'CPU: %s\n' "$(lscpu | grep "Model name" | cut -d: -f2 | xargs)"
printf 'Kernel: %s\n' "$(uname -r)"
printf 'OS: %s\n' "$(cat /etc/os-release | grep PRETTY_NAME | cut -d= -f2 | tr -d '"')"

print_header "1. Checking IOMMU Status"
if grep -q "intel_iommu=on" /proc/cmdline; then
    print_ok "Intel IOMMU enabled in kernel parameters"
else
    print_error "Intel IOMMU NOT enabled"
    ((ERRORS++))
fi

if grep -q "iommu=pt" /proc/cmdline; then
    print_ok "IOMMU passthrough mode enabled"
else
    print_warn "IOMMU passthrough mode not set (optional)"
    ((WARNINGS++))
fi

print_header "2. Checking GPU Configuration"
GPU_INFO=$(lspci -nn | grep -i "VGA.*NVIDIA")
if [[ -n "$GPU_INFO" ]]; then
    print_ok "NVIDIA GPU found: $GPU_INFO"
    GPU_PCI=$(echo "$GPU_INFO" | cut -d' ' -f1)
    printf '   PCI Address: %s\n' "$GPU_PCI"
else
    print_error "No NVIDIA GPU found"
    ((ERRORS++))
fi

AUDIO_INFO=$(lspci -nn | grep -i "Audio.*NVIDIA")
if [[ -n "$AUDIO_INFO" ]]; then
    print_ok "NVIDIA Audio found: $AUDIO_INFO"
    AUDIO_PCI=$(echo "$AUDIO_INFO" | cut -d' ' -f1)
    printf '   PCI Address: %s\n' "$AUDIO_PCI"
else
    print_warn "No NVIDIA Audio device found"
    ((WARNINGS++))
fi

# Check current driver
CURRENT_DRIVER=$(lspci -k -s "$GPU_PCI" | grep "Kernel driver in use" | cut -d: -f2 | xargs)
if [[ "$CURRENT_DRIVER" == "nvidia" ]]; then
    print_ok "GPU currently using nvidia driver: $CURRENT_DRIVER"
else
    print_warn "GPU driver is: $CURRENT_DRIVER (expected nvidia)"
    ((WARNINGS++))
fi

print_header "3. Checking Display Manager"
if systemctl is-active --quiet sddm; then
    print_ok "SDDM is active"
    DM="sddm"
elif systemctl is-active --quiet gdm; then
    print_ok "GDM is active"
    DM="gdm"
elif systemctl is-active --quiet lightdm; then
    print_ok "LightDM is active"
    DM="lightdm"
else
    print_error "No known display manager is active"
    ((ERRORS++))
    DM="unknown"
fi

print_header "4. Checking SSH Configuration"
if systemctl is-active --quiet sshd; then
    print_ok "SSH daemon is running"
else
    print_error "SSH daemon is NOT running (CRITICAL for recovery!)"
    ((ERRORS++))
fi

IP_ADDR=$(ip -4 addr show | grep "inet " | grep -v 127.0.0.1 | head -1 | awk '{print $2}' | cut -d/ -f1)
if [[ -n "$IP_ADDR" ]]; then
    print_ok "Local IP address: $IP_ADDR"
    printf '   Test SSH with: ssh %s@%s\n' "$USER" "$IP_ADDR"
else
    print_warn "Could not determine local IP address"
    ((WARNINGS++))
fi

print_header "5. Checking Virtualization"
if command -v virt-manager >/dev/null 2>&1; then
    print_ok "virt-manager is installed"
else
    print_error "virt-manager is NOT installed"
    ((ERRORS++))
fi

if command -v virsh >/dev/null 2>&1; then
    print_ok "virsh is installed"
else
    print_error "virsh is NOT installed"
    ((ERRORS++))
fi

if systemctl is-active --quiet libvirtd; then
    print_ok "libvirtd is running"
else
    print_warn "libvirtd is NOT running (will start when needed)"
    printf '   Start with: sudo systemctl start libvirtd\n'
    ((WARNINGS++))
fi

# Check if user is in libvirt group
if groups | grep -q libvirt; then
    print_ok "User is in libvirt group"
else
    print_warn "User is NOT in libvirt group"
    printf '   Add with: sudo usermod -aG libvirt %s\n' "$USER"
    ((WARNINGS++))
fi

print_header "6. Checking Required Directories"
if [[ -d /etc/libvirt/hooks ]]; then
    print_ok "/etc/libvirt/hooks exists"
else
    print_warn "/etc/libvirt/hooks does not exist (will be created)"
    ((WARNINGS++))
fi

if [[ -d /var/lib/libvirt/images ]]; then
    print_ok "/var/lib/libvirt/images exists"
else
    print_warn "/var/lib/libvirt/images does not exist"
    ((WARNINGS++))
fi

print_header "7. Checking for ISOs"
WIN_ISO=$(find ~ -iname "*win11*.iso" -o -iname "*windows*11*.iso" 2>/dev/null | head -1)
if [[ -n "$WIN_ISO" ]]; then
    print_ok "Windows ISO found: $WIN_ISO"
else
    print_warn "No Windows 11 ISO found in home directory"
    ((WARNINGS++))
fi

VIRTIO_ISO=$(find /var/lib/libvirt /usr/share ~ -iname "*virtio*win*.iso" 2>/dev/null | head -1)
if [[ -n "$VIRTIO_ISO" ]]; then
    print_ok "VirtIO ISO found: $VIRTIO_ISO"
else
    print_warn "No VirtIO ISO found"
    printf '   Download from: https://fedorapeople.org/groups/virt/virtio-win/direct-downloads/latest-virtio/\n'
    ((WARNINGS++))
fi

print_header "8. Checking Kernel Modules"
if lsmod | grep -q "^nvidia "; then
    print_ok "nvidia module is loaded"
else
    print_warn "nvidia module is not loaded"
    ((WARNINGS++))
fi

if lsmod | grep -q "^vfio_pci"; then
    print_warn "vfio_pci module is already loaded (should not be at boot)"
    ((WARNINGS++))
else
    print_ok "vfio_pci module is not loaded (correct)"
fi

if lsmod | grep -q "^kvm_intel"; then
    print_ok "kvm_intel module is loaded"
else
    print_error "kvm_intel module is NOT loaded"
    ((ERRORS++))
fi

print_header "Summary"
if [[ $ERRORS -eq 0 && $WARNINGS -eq 0 ]]; then
    print_ok "All checks passed! System is ready for GPU passthrough."
elif [[ $ERRORS -eq 0 ]]; then
    print_warn "System is ready with $WARNINGS warning(s)"
else
    print_error "System has $ERRORS critical error(s) and $WARNINGS warning(s)"
    printf '\nFix critical errors before attempting GPU passthrough!\n'
    exit 1
fi

printf '\n%bConfiguration Summary:%b\n' "$BLUE" "$NC"
printf 'GPU PCI:        0000:%s\n' "$GPU_PCI"
printf 'Audio PCI:      0000:%s\n' "$AUDIO_PCI"
printf 'Display Mgr:    %s\n' "$DM"
printf 'SSH IP:         %s\n' "$IP_ADDR"
printf 'Current Driver: %s\n' "$CURRENT_DRIVER"

printf '\n%bNext Steps:%b\n' "$GREEN" "$NC"
printf '1. Ensure SSH is accessible from another device\n'
printf '2. Create hook scripts with timeout\n'
printf '3. Set up Windows VM\n'
printf '4. Test GPU passthrough with automatic revert\n'
