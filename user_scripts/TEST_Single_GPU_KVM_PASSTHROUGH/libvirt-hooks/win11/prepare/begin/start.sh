#!/usr/bin/env bash
#
# VM Prepare Hook - Unbind GPU from host, bind to vfio-pci
# This script runs BEFORE the VM starts
#
# SAFETY TIMEOUT: Set TIMEOUT_MINUTES environment variable to auto-shutdown VM
#
set -euo pipefail

# Configuration - ADJUST THESE FOR YOUR SYSTEM
readonly GPU_PCI="0000:01:00.0"
readonly GPU_AUDIO_PCI="0000:01:00.1"
readonly DISPLAY_MANAGER="sddm"

# Safety timeout (in minutes) - set via environment or default to 5
readonly TIMEOUT_MINUTES="${GPU_PASSTHROUGH_TIMEOUT:-5}"
readonly VM_NAME="$1"

# Logging - logs go to journald, viewable with: journalctl -t vm-gpu-start
exec 1> >(logger -s -t "vm-gpu-start") 2>&1

printf '========================================\n'
printf 'GPU Passthrough: Starting for VM %s\n' "$VM_NAME"
printf 'Timeout: %d minutes\n' "$TIMEOUT_MINUTES"
printf 'Time: %s\n' "$(date '+%Y-%m-%d %H:%M:%S')"
printf '========================================\n'

# Verify PCI devices exist
if [[ ! -d "/sys/bus/pci/devices/$GPU_PCI" ]]; then
    printf 'ERROR: GPU PCI device not found: %s\n' "$GPU_PCI" >&2
    printf 'Run: lspci -nn | grep -i nvidia to find correct address\n' >&2
    exit 1
fi

if [[ ! -d "/sys/bus/pci/devices/$GPU_AUDIO_PCI" ]]; then
    printf 'ERROR: GPU Audio PCI device not found: %s\n' "$GPU_AUDIO_PCI" >&2
    exit 1
fi

# Stop display manager
printf 'Stopping display manager: %s\n' "$DISPLAY_MANAGER"
if ! systemctl stop "$DISPLAY_MANAGER"; then
    printf 'ERROR: Failed to stop %s\n' "$DISPLAY_MANAGER" >&2
    exit 1
fi

# Wait for display manager to fully stop
sleep 3

# Unbind VT consoles
printf 'Unbinding VT consoles\n'
echo 0 > /sys/class/vtconsole/vtcon0/bind || true
echo 0 > /sys/class/vtconsole/vtcon1/bind 2>/dev/null || true

# Unbind EFI framebuffer
echo efi-framebuffer.0 > /sys/bus/platform/drivers/efi-framebuffer/unbind 2>/dev/null || true

# Check what's using nvidia before unloading
printf 'Checking for processes using nvidia\n'
if lsof /dev/nvidia* 2>/dev/null; then
    printf 'WARNING: Processes are using the GPU. Attempting to unload anyway...\n'
fi

# Unload nvidia modules
printf 'Unloading nvidia modules\n'
if ! modprobe -r nvidia_drm nvidia_modeset nvidia_uvm nvidia; then
    printf 'ERROR: Failed to unload nvidia modules\n' >&2
    printf 'Processes using GPU:\n' >&2
    lsof /dev/nvidia* >&2 || true
    printf 'Restoring display manager\n' >&2
    systemctl start "$DISPLAY_MANAGER"
    exit 1
fi

# Unbind GPU from host driver
printf 'Unbinding GPU from host driver\n'
if [[ -e "/sys/bus/pci/devices/$GPU_PCI/driver" ]]; then
    echo "$GPU_PCI" > "/sys/bus/pci/devices/$GPU_PCI/driver/unbind"
fi

if [[ -e "/sys/bus/pci/devices/$GPU_AUDIO_PCI/driver" ]]; then
    echo "$GPU_AUDIO_PCI" > "/sys/bus/pci/devices/$GPU_AUDIO_PCI/driver/unbind"
fi

# Load vfio modules
printf 'Loading vfio modules\n'
modprobe vfio
modprobe vfio_pci
modprobe vfio_iommu_type1

# Bind GPU to vfio-pci
printf 'Binding GPU to vfio-pci\n'
echo vfio-pci > "/sys/bus/pci/devices/$GPU_PCI/driver_override"
echo vfio-pci > "/sys/bus/pci/devices/$GPU_AUDIO_PCI/driver_override"

if ! echo "$GPU_PCI" > /sys/bus/pci/drivers/vfio-pci/bind; then
    printf 'ERROR: Failed to bind GPU to vfio-pci\n' >&2
    # Attempt to restore host display
    echo "" > "/sys/bus/pci/devices/$GPU_PCI/driver_override"
    modprobe nvidia
    systemctl start "$DISPLAY_MANAGER"
    exit 1
fi

if ! echo "$GPU_AUDIO_PCI" > /sys/bus/pci/drivers/vfio-pci/bind; then
    printf 'ERROR: Failed to bind GPU audio to vfio-pci\n' >&2
    exit 1
fi

printf 'GPU successfully bound to vfio-pci\n'

# Start safety timeout timer (runs in background)
if (( TIMEOUT_MINUTES > 0 )); then
    printf 'Starting safety timeout timer (%d minutes)\n' "$TIMEOUT_MINUTES"
    (
        sleep $((TIMEOUT_MINUTES * 60))
        logger -t "vm-gpu-timeout" "Safety timeout reached for VM $VM_NAME - forcing shutdown"
        virsh shutdown "$VM_NAME" || virsh destroy "$VM_NAME"
    ) &
    TIMEOUT_PID=$!
    printf 'Safety timer PID: %d\n' "$TIMEOUT_PID"
    # Store PID for potential cleanup
    echo "$TIMEOUT_PID" > "/tmp/gpu-passthrough-timeout-${VM_NAME}.pid"
fi

printf 'VM can now start. Display will be BLACK on host.\n'
printf 'Monitor will show VM output when VM boots.\n'
printf '========================================\n'
