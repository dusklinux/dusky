#!/usr/bin/env bash
#
# VM Release Hook - Unbind GPU from vfio-pci, return to host
# This script runs AFTER the VM stops
#
set -euo pipefail

# Configuration - ADJUST THESE FOR YOUR SYSTEM
readonly GPU_PCI="0000:01:00.0"
readonly GPU_AUDIO_PCI="0000:01:00.1"
readonly DISPLAY_MANAGER="sddm"
readonly VM_NAME="$1"

# Logging - logs go to journald, viewable with: journalctl -t vm-gpu-stop
exec 1> >(logger -s -t "vm-gpu-stop") 2>&1

printf '========================================\n'
printf 'GPU Passthrough: Stopping for VM %s\n' "$VM_NAME"
printf 'Time: %s\n' "$(date '+%Y-%m-%d %H:%M:%S')"
printf '========================================\n'

# Kill safety timeout timer if it exists
if [[ -f "/tmp/gpu-passthrough-timeout-${VM_NAME}.pid" ]]; then
    TIMEOUT_PID=$(cat "/tmp/gpu-passthrough-timeout-${VM_NAME}.pid")
    if kill -0 "$TIMEOUT_PID" 2>/dev/null; then
        printf 'Stopping safety timeout timer (PID: %d)\n' "$TIMEOUT_PID"
        kill "$TIMEOUT_PID" || true
    fi
    rm -f "/tmp/gpu-passthrough-timeout-${VM_NAME}.pid"
fi

# Unbind from vfio-pci
printf 'Unbinding GPU from vfio-pci\n'
echo "$GPU_PCI" > /sys/bus/pci/drivers/vfio-pci/unbind 2>/dev/null || true
echo "$GPU_AUDIO_PCI" > /sys/bus/pci/drivers/vfio-pci/unbind 2>/dev/null || true

# Clear driver override
printf 'Clearing driver override\n'
echo "" > "/sys/bus/pci/devices/$GPU_PCI/driver_override"
echo "" > "/sys/bus/pci/devices/$GPU_AUDIO_PCI/driver_override"

# Unload vfio modules
printf 'Unloading vfio modules\n'
modprobe -r vfio_pci || true
modprobe -r vfio_iommu_type1 || true
modprobe -r vfio || true

# Rescan PCI bus to detect GPU
printf 'Rescanning PCI bus\n'
echo 1 > /sys/bus/pci/rescan

# Wait for device detection
sleep 3

# Reload nvidia modules
printf 'Loading nvidia modules\n'
if ! modprobe nvidia; then
    printf 'ERROR: Failed to load nvidia module\n' >&2
    exit 1
fi
modprobe nvidia_modeset
modprobe nvidia_uvm
modprobe nvidia_drm

# Rebind VT consoles
printf 'Rebinding VT consoles\n'
echo 1 > /sys/class/vtconsole/vtcon0/bind || true
echo 1 > /sys/class/vtconsole/vtcon1/bind 2>/dev/null || true

# Rebind EFI framebuffer
echo efi-framebuffer.0 > /sys/bus/platform/drivers/efi-framebuffer/bind 2>/dev/null || true

# Start display manager
printf 'Starting display manager: %s\n' "$DISPLAY_MANAGER"
if ! systemctl start "$DISPLAY_MANAGER"; then
    printf 'ERROR: Failed to start %s\n' "$DISPLAY_MANAGER" >&2
    printf 'Try manually: sudo systemctl start %s\n' "$DISPLAY_MANAGER" >&2
    exit 1
fi

printf 'GPU successfully returned to host\n'
printf 'Display should be restored within 10 seconds\n'
printf '========================================\n'
