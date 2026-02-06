#!/usr/bin/env bash
#
# Test GPU Bind/Unbind Without VM
# This script simulates the GPU passthrough process without starting a VM
# It will bind the GPU to vfio-pci, wait for a timeout, then restore it
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

print_warn() {
    printf '%b⚠%b %s\n' "$YELLOW" "$NC" "$1"
}

print_error() {
    printf '%b✗%b %s\n' "$RED" "$NC" "$1"
}

# Check if running as root
if [[ $EUID -ne 0 ]]; then
    print_error "This script must be run as root (use sudo)"
    exit 1
fi

# Configuration
readonly GPU_PCI="0000:01:00.0"
readonly GPU_AUDIO_PCI="0000:01:00.1"
readonly DISPLAY_MANAGER="sddm"
readonly TEST_DURATION="${1:-30}"  # Seconds to stay in vfio-pci mode

print_header "GPU Bind/Unbind Test"
printf 'Test Duration: %d seconds\n' "$TEST_DURATION"
printf 'GPU: %s\n' "$GPU_PCI"
printf 'Audio: %s\n' "$GPU_AUDIO_PCI"
printf '\n%bWARNING: Your display will go BLACK during this test!%b\n' "$YELLOW" "$NC"
printf 'Have SSH ready on another device: ssh %s@10.10.10.9\n' "$SUDO_USER"
printf '\nPress Ctrl+C now to abort, or Enter to continue...'
read -r

# Cleanup function
cleanup() {
    printf '\n%bRestoring GPU to host...%b\n' "$YELLOW" "$NC"

    # Unbind from vfio-pci
    echo "$GPU_PCI" > /sys/bus/pci/drivers/vfio-pci/unbind 2>/dev/null || true
    echo "$GPU_AUDIO_PCI" > /sys/bus/pci/drivers/vfio-pci/unbind 2>/dev/null || true

    # Clear driver override
    echo "" > "/sys/bus/pci/devices/$GPU_PCI/driver_override"
    echo "" > "/sys/bus/pci/devices/$GPU_AUDIO_PCI/driver_override"

    # Unload vfio modules
    modprobe -r vfio_pci || true
    modprobe -r vfio_iommu_type1 || true
    modprobe -r vfio || true

    # Rescan PCI bus
    echo 1 > /sys/bus/pci/rescan
    sleep 3

    # Reload nvidia
    modprobe nvidia
    modprobe nvidia_modeset
    modprobe nvidia_uvm
    modprobe nvidia_drm

    # Rebind consoles
    echo 1 > /sys/class/vtconsole/vtcon0/bind || true
    echo 1 > /sys/class/vtconsole/vtcon1/bind 2>/dev/null || true
    echo efi-framebuffer.0 > /sys/bus/platform/drivers/efi-framebuffer/bind 2>/dev/null || true

    # Start display manager
    systemctl start "$DISPLAY_MANAGER"

    print_ok "GPU restored to host"
}

# Set trap for cleanup on exit
trap cleanup EXIT INT TERM

print_header "Phase 1: Unbinding GPU from Host"

# Stop display manager
print_ok "Stopping display manager"
systemctl stop "$DISPLAY_MANAGER"
sleep 3

# Unbind consoles
print_ok "Unbinding VT consoles"
echo 0 > /sys/class/vtconsole/vtcon0/bind || true
echo 0 > /sys/class/vtconsole/vtcon1/bind 2>/dev/null || true
echo efi-framebuffer.0 > /sys/bus/platform/drivers/efi-framebuffer/unbind 2>/dev/null || true

# Unload nvidia
print_ok "Unloading nvidia modules"
modprobe -r nvidia_drm nvidia_modeset nvidia_uvm nvidia

# Unbind GPU
print_ok "Unbinding GPU from driver"
if [[ -e "/sys/bus/pci/devices/$GPU_PCI/driver" ]]; then
    echo "$GPU_PCI" > "/sys/bus/pci/devices/$GPU_PCI/driver/unbind"
fi
if [[ -e "/sys/bus/pci/devices/$GPU_AUDIO_PCI/driver" ]]; then
    echo "$GPU_AUDIO_PCI" > "/sys/bus/pci/devices/$GPU_AUDIO_PCI/driver/unbind"
fi

print_header "Phase 2: Binding GPU to vfio-pci"

# Load vfio
print_ok "Loading vfio modules"
modprobe vfio
modprobe vfio_pci
modprobe vfio_iommu_type1

# Bind to vfio-pci
print_ok "Binding GPU to vfio-pci"
echo vfio-pci > "/sys/bus/pci/devices/$GPU_PCI/driver_override"
echo vfio-pci > "/sys/bus/pci/devices/$GPU_AUDIO_PCI/driver_override"
echo "$GPU_PCI" > /sys/bus/pci/drivers/vfio-pci/bind
echo "$GPU_AUDIO_PCI" > /sys/bus/pci/drivers/vfio-pci/bind

# Verify binding
DRIVER=$(lspci -k -s "01:00.0" | grep "Kernel driver in use" | cut -d: -f2 | xargs)
if [[ "$DRIVER" == "vfio-pci" ]]; then
    print_ok "GPU successfully bound to vfio-pci"
else
    print_error "GPU binding failed! Driver is: $DRIVER"
    exit 1
fi

print_header "Phase 3: Waiting"
printf 'GPU is now bound to vfio-pci (VM would use it now)\n'
printf 'Waiting %d seconds before restoring...\n' "$TEST_DURATION"

for ((i=TEST_DURATION; i>0; i--)); do
    printf '\rRestoring in %2d seconds... ' "$i"
    sleep 1
done
printf '\n'

print_header "Phase 4: Restoring GPU to Host"
printf 'Cleanup trap will restore the GPU...\n'

# Exit will trigger cleanup trap
exit 0
