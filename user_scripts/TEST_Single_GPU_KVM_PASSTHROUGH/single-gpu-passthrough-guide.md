# Single GPU Passthrough Guide

Pass your only GPU to a VM on-demand, without breaking host boot.

## ⚠️ Know Your Hardware First

**Do you have integrated graphics?**

Run this command to check:
```bash
lscpu | grep "Model name"
lspci | grep -i vga
```

**If you see:**
- Intel CPU ending in **F** (i7-14700KF, i5-13600KF): ❌ **NO** integrated GPU
- Intel CPU without F (i7-14700K, i9-13900K): ✅ **HAS** integrated GPU
- AMD CPU ending in **G** (5600G, 5700G): ✅ **HAS** integrated GPU (APU)
- AMD CPU without G (5800X, 7950X): ❌ **NO** integrated GPU
- Only one entry in `lspci | grep -i vga`: ❌ Single GPU system

### What This Means for You

| Your System | When VM Runs | Host Display | Best Viewing Method |
|-------------|--------------|--------------|---------------------|
| **No iGPU** (F-series Intel, most AMD) | Host becomes headless | ❌ None | Physical monitor shows VM directly |
| **Has iGPU** (non-F Intel, AMD APU) | Host keeps display on iGPU | ✅ Working | Looking Glass (view VM in window) |

**If you have no iGPU:** Read Step 0 carefully and set up SSH BEFORE attempting passthrough.

## How It Works

Instead of binding the GPU to vfio-pci at boot (which breaks display), we:
1. Boot normally with nvidia driver
2. When starting the VM: stop display manager → unbind nvidia → bind vfio-pci → start VM
3. When stopping the VM: unbind vfio-pci → bind nvidia → start display manager

This is achieved with **libvirt hooks** - scripts that run before/after VM start/stop.

## Prerequisites

- IOMMU enabled in BIOS (VT-d for Intel, AMD-Vi for AMD)
- libvirt, qemu, virt-manager installed
- Your VM already configured (we'll add the GPU later)

## ⚠️ CRITICAL: Systems WITHOUT Integrated Graphics

**If your CPU has NO integrated GPU (Intel F-series like i7-14700KF, or AMD CPUs without graphics):**

When you pass through your only GPU to the VM:
- **Your host display will go COMPLETELY BLACK**
- The host becomes **headless** (cannot render any graphics)
- Your physical monitor will show **only the VM's output**
- You **MUST** set up SSH access for emergency recovery

**This is NOT optional.** Read Step 0 below before proceeding.

## Step 0: Emergency Recovery Setup (MANDATORY for No-iGPU Systems)

### 1. Enable and Test SSH

```bash
# Enable SSH daemon
sudo systemctl enable --now sshd

# Verify it's running
sudo systemctl status sshd

# Get your local IP address (write this down!)
ip -4 addr show | grep "inet " | grep -v 127.0.0.1
# Example output: inet 192.168.1.100/24
```

### 2. Test SSH from Another Device

From a phone (using Termux), laptop, or another computer on the same network:

```bash
ssh your-username@192.168.1.100  # Use your actual IP
```

If this doesn't work, **DO NOT proceed** with GPU passthrough until SSH is working.

### 3. Create Emergency Recovery Script

```bash
mkdir -p ~/.local/bin

cat > ~/.local/bin/gpu-recovery << 'EOF'
#!/usr/bin/env bash
set -euo pipefail
sudo /etc/libvirt/hooks/qemu.d/win11/release/end/stop.sh
sudo systemctl start sddm  # Change to your display manager
EOF

chmod +x ~/.local/bin/gpu-recovery

# Add to PATH if not already
echo 'export PATH="$HOME/.local/bin:$PATH"' >> ~/.bashrc
```

### 4. Test the Recovery Script

```bash
source ~/.bashrc
gpu-recovery  # Should run without errors
```

**Recovery procedure if display goes black:**
1. SSH from another device: `ssh user@192.168.1.100`
2. Run: `gpu-recovery`
3. Your display should return

## Step 1: Enable IOMMU (Keep This)

The `intel_iommu=on iommu=pt` kernel parameters are fine to keep. They enable IOMMU without binding anything to vfio.

Verify IOMMU is working:
```bash
dmesg | grep -i iommu
# Should show IOMMU enabled messages
```

## Step 2: DO NOT Configure Early vfio-pci Binding

**This is what caused your boot issue.** Make sure these are NOT set:

```bash
# /etc/mkinitcpio.conf - MODULES should be empty or not include vfio
MODULES=()

# /etc/modprobe.d/vfio.conf - should NOT exist or be empty
# DELETE this file if it exists
```

## Step 3: Identify Your GPU's PCI Addresses

```bash
# Find your GPU
lspci -nn | grep -i nvidia
# Example output:
# 01:00.0 VGA compatible controller [0300]: NVIDIA Corporation AD102 [GeForce RTX 4090] [10de:2684]
# 01:00.1 Audio device [0403]: NVIDIA Corporation AD102 High Definition Audio Controller [10de:22ba]
```

Note the addresses: `01:00.0` (GPU) and `01:00.1` (audio). The full PCI path is `pci_0000_01_00_0`.

## Step 4: Create the Libvirt Hooks Directory Structure

```bash
sudo mkdir -p /etc/libvirt/hooks/qemu.d/win11/prepare/begin
sudo mkdir -p /etc/libvirt/hooks/qemu.d/win11/release/end
```

Replace `win11` with your VM's exact name as shown in virt-manager.

## Step 5: Create the Main Hook Script

```bash
sudo nano /etc/libvirt/hooks/qemu
```

```bash
#!/usr/bin/env bash
#
# Libvirt QEMU Hook Dispatcher
# Executes hook scripts based on VM lifecycle events
#
set -euo pipefail

GUEST_NAME="$1"
HOOK_NAME="$2"
STATE_NAME="$3"

BASEDIR="$(dirname "$0")"
HOOK_PATH="$BASEDIR/qemu.d/$GUEST_NAME/$HOOK_NAME/$STATE_NAME"

if [[ -f "$HOOK_PATH" ]]; then
    "$HOOK_PATH" "$@"
elif [[ -d "$HOOK_PATH" ]]; then
    while read -r file; do
        "$file" "$@"
    done <<< "$(find -L "$HOOK_PATH" -maxdepth 1 -type f -executable | sort)"
fi
```

```bash
sudo chmod +x /etc/libvirt/hooks/qemu
```

## Step 6: Create the VM Start Script

```bash
sudo nano /etc/libvirt/hooks/qemu.d/win11/prepare/begin/start.sh
```

```bash
#!/usr/bin/env bash
#
# VM Prepare Hook - Unbind GPU from host, bind to vfio-pci
# This script runs BEFORE the VM starts
#
set -euo pipefail

# Configuration - ADJUST THESE FOR YOUR SYSTEM
readonly GPU_PCI="0000:01:00.0"
readonly GPU_AUDIO_PCI="0000:01:00.1"
readonly DISPLAY_MANAGER="sddm"  # or gdm, lightdm, etc.

# Logging - logs go to journald, viewable with: journalctl -t vm-gpu-start
exec 1> >(logger -s -t "vm-gpu-start") 2>&1

printf 'Starting GPU passthrough preparation\n'

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
systemctl stop "$DISPLAY_MANAGER" || {
    printf 'ERROR: Failed to stop %s\n' "$DISPLAY_MANAGER" >&2
    exit 1
}

# Wait for display manager to fully stop
sleep 3

# Unbind VT consoles
printf 'Unbinding VT consoles\n'
echo 0 > /sys/class/vtconsole/vtcon0/bind || true
echo 0 > /sys/class/vtconsole/vtcon1/bind 2>/dev/null || true

# Unbind EFI framebuffer
echo efi-framebuffer.0 > /sys/bus/platform/drivers/efi-framebuffer/unbind 2>/dev/null || true

# Unload nvidia modules
printf 'Unloading nvidia modules\n'
modprobe -r nvidia_drm nvidia_modeset nvidia_uvm nvidia || {
    printf 'ERROR: Failed to unload nvidia modules. Check what is using the GPU:\n' >&2
    lsof /dev/nvidia* >&2 || true
    printf 'Close all applications using the GPU and try again\n' >&2
    systemctl start "$DISPLAY_MANAGER"  # Restore display
    exit 1
}

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

echo "$GPU_PCI" > /sys/bus/pci/drivers/vfio-pci/bind || {
    printf 'ERROR: Failed to bind GPU to vfio-pci\n' >&2
    # Attempt to restore host display
    echo "" > "/sys/bus/pci/devices/$GPU_PCI/driver_override"
    modprobe nvidia
    systemctl start "$DISPLAY_MANAGER"
    exit 1
}

echo "$GPU_AUDIO_PCI" > /sys/bus/pci/drivers/vfio-pci/bind || {
    printf 'ERROR: Failed to bind GPU audio to vfio-pci\n' >&2
    exit 1
}

printf 'GPU successfully bound to vfio-pci. VM can now start.\n'
```

```bash
sudo chmod +x /etc/libvirt/hooks/qemu.d/win11/prepare/begin/start.sh
```

## Step 7: Create the VM Stop Script

```bash
sudo nano /etc/libvirt/hooks/qemu.d/win11/release/end/stop.sh
```

```bash
#!/usr/bin/env bash
#
# VM Release Hook - Unbind GPU from vfio-pci, return to host
# This script runs AFTER the VM stops
#
set -euo pipefail

# Configuration - ADJUST THESE FOR YOUR SYSTEM
readonly GPU_PCI="0000:01:00.0"
readonly GPU_AUDIO_PCI="0000:01:00.1"
readonly DISPLAY_MANAGER="sddm"  # or gdm, lightdm, etc.

# Logging - logs go to journald, viewable with: journalctl -t vm-gpu-stop
exec 1> >(logger -s -t "vm-gpu-stop") 2>&1

printf 'Starting GPU return to host\n'

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
modprobe -r vfio_pci
modprobe -r vfio_iommu_type1
modprobe -r vfio

# Rescan PCI bus to detect GPU
printf 'Rescanning PCI bus\n'
echo 1 > /sys/bus/pci/rescan

# Wait for device detection
sleep 3

# Reload nvidia modules
printf 'Loading nvidia modules\n'
modprobe nvidia || {
    printf 'ERROR: Failed to load nvidia module\n' >&2
    exit 1
}
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
systemctl start "$DISPLAY_MANAGER" || {
    printf 'ERROR: Failed to start %s\n' "$DISPLAY_MANAGER" >&2
    printf 'Try manually: sudo systemctl start %s\n' "$DISPLAY_MANAGER" >&2
    exit 1
}

printf 'GPU successfully returned to host. Display should be restored.\n'
```

```bash
sudo chmod +x /etc/libvirt/hooks/qemu.d/win11/release/end/stop.sh
```

## Step 8: Add GPU to VM Configuration

Edit your VM's XML configuration:

```bash
sudo virsh edit win11
```

Add the GPU and audio devices inside the `<devices>` section:

```xml
<hostdev mode='subsystem' type='pci' managed='yes'>
  <source>
    <address domain='0x0000' bus='0x01' slot='0x00' function='0x0'/>
  </source>
  <address type='pci' domain='0x0000' bus='0x06' slot='0x00' function='0x0'/>
</hostdev>
<hostdev mode='subsystem' type='pci' managed='yes'>
  <source>
    <address domain='0x0000' bus='0x01' slot='0x00' function='0x1'/>
  </source>
  <address type='pci' domain='0x0000' bus='0x07' slot='0x00' function='0x0'/>
</hostdev>
```

Adjust the bus/slot/function to match your `lspci` output (01:00.0 = bus 0x01, slot 0x00, function 0x0).

## Step 9: Restart libvirtd

```bash
sudo systemctl restart libvirtd
```

## Step 10: Test It

**Before starting:** Have an SSH session ready on another device, or accept that your monitor will show the VM directly.

### For Systems WITHOUT iGPU (i7-14700KF):

1. **Open an SSH session from another device** (phone/laptop):
   ```bash
   ssh user@192.168.1.100  # Your host's IP
   ```

2. **From virt-manager on the host, start your Windows VM**

3. **Your display will go BLACK** - this is normal! The host no longer has graphics capability.

4. **Wait 30-60 seconds** - your monitor should show the Windows boot screen

5. **Your monitor now displays the VM directly** - use it like a normal Windows PC

6. **To control the host:** Use the SSH session from step 1

7. **To stop the VM:** Either shut down Windows normally, or from SSH:
   ```bash
   sudo virsh shutdown win11
   ```

8. **After VM stops:** Your Linux desktop should return automatically

### For Systems WITH iGPU:

1. Open virt-manager
2. Start your Windows VM
3. Your display might flicker briefly
4. Use Looking Glass or physical monitor to see the VM
5. When you shut down the VM, your Linux desktop continues normally

### Expected Timeline:

```
0s:   Click "Start" in virt-manager
2s:   Display manager stops, screen goes black
5s:   Hook script completes, VM begins booting
15s:  Windows boot logo appears on monitor
30s:  Windows desktop ready
```

### If Something Goes Wrong:

**Display stays black after 2 minutes:**

From SSH:
```bash
# Check if VM is running
sudo virsh list --all

# If VM is running but no display:
# Check VM logs
sudo tail -f /var/log/libvirt/qemu/win11.log

# Force stop VM and restore display
sudo virsh destroy win11
gpu-recovery
```

**Can't SSH in:**

1. Press `Ctrl+Alt+F2` to try accessing a TTY
2. Login and run: `gpu-recovery`
3. If TTY doesn't work, hard reboot (hold power button)

## Viewing the VM Display

Since your GPU is passed to the VM, you need a way to see the VM's output.

**The method you choose depends on whether your CPU has integrated graphics:**

### For CPUs WITHOUT Integrated Graphics (i7-14700KF, AMD F-series)

When the VM starts, your host becomes **completely headless** (no display capability at all).

#### Option 1: Physical Monitor (Recommended - Zero Latency)

**This is the standard approach for single GPU passthrough.**

- Your physical monitor(s) connected to the GPU will display the VM directly
- When VM starts: monitor goes black → shows Windows boot screen
- When VM stops: monitor shows Linux desktop again
- To control the host while VM is running: SSH from another device

**Setup:** None needed - just use your existing monitor(s).

#### Option 2: Network Streaming (Sunshine + Moonlight)

Stream the VM's display to another device over your local network.

**In the Windows VM:**
1. Download and install [Sunshine](https://github.com/LizardByte/Sunshine/releases)
2. Configure Sunshine and set a PIN

**On your viewing device (phone/tablet/laptop):**
1. Install Moonlight client
2. Connect to the VM's IP address
3. Enter the PIN

**Latency:** Adds ~10-20ms, suitable for most gaming.

#### Option 3: Remote Desktop (RDP)

**In Windows VM:**
- Enable Remote Desktop in Windows Settings
- Note the VM's IP address

**From another device:**
```bash
# Linux
rdesktop <vm-ip>

# Or use Remmina GUI
```

**Latency:** Higher (~50-100ms), not ideal for gaming.

---

### ❌ Looking Glass - NOT Compatible Without iGPU

**Looking Glass REQUIRES the host to have display capability** (integrated GPU or second discrete GPU).

If your CPU has no iGPU (F-series Intel, many AMD chips), Looking Glass **will not work** because:
- The host cannot render the Looking Glass window (no GPU available)
- When your only GPU passes to the VM, the host becomes headless

**Looking Glass is only for:**
- Dual GPU systems (iGPU + dGPU passing through)
- Systems with multiple discrete GPUs (one for host, one for VM)

If you have an iGPU, Looking Glass is excellent. If not, use physical monitor or network streaming.

---

### For CPUs WITH Integrated Graphics (Most Intel non-F, AMD APUs)

#### Option: Looking Glass (Recommended - Low Latency)

Looking Glass lets you view the VM in a window on your host desktop.

**Install in VM:** [Looking Glass Host](https://looking-glass.io/downloads)

**Install on host:**
```bash
paru -S looking-glass  # or build from source
```

**Add shared memory to VM XML:**
```bash
sudo virsh edit win11
```

Add inside `<devices>`:
```xml
<shmem name='looking-glass'>
  <model type='ivshmem-plain'/>
  <size unit='M'>64</size>
</shmem>
```

**Create shared memory file:**
```bash
sudo touch /dev/shm/looking-glass
sudo chown $USER:kvm /dev/shm/looking-glass
sudo chmod 660 /dev/shm/looking-glass
```

**Make it persistent (create systemd tmpfile):**
```bash
echo 'f /dev/shm/looking-glass 0660 $USER kvm -' | sudo tee /etc/tmpfiles.d/looking-glass.conf
```

**Run Looking Glass client:**
```bash
looking-glass-client
```

The VM will display in a window on your host desktop.

## Troubleshooting

### Viewing Logs

The hook scripts now log to journald. View them with:

```bash
# View start hook logs
sudo journalctl -t vm-gpu-start -n 50

# View stop hook logs
sudo journalctl -t vm-gpu-stop -n 50

# Follow logs in real-time
sudo journalctl -t vm-gpu-start -t vm-gpu-stop -f

# View libvirt logs
sudo journalctl -u libvirtd -n 50

# View VM console output
sudo tail -f /var/log/libvirt/qemu/win11.log
```

### VM won't start, display manager keeps restarting

**Symptom:** Display flickers black, then returns to login screen immediately.

**Cause:** Another process is using the GPU.

**Fix:**
```bash
# Check what's using the GPU
lsof /dev/nvidia*

# Common culprits:
# - Wayland compositors (use X11 instead, or stop compositor first)
# - Steam
# - Discord (hardware acceleration)
# - Chrome/Firefox (hardware acceleration)
# - Conky or other desktop widgets

# Close those apps, then try again
# Or add a longer sleep in start.sh:
sleep 5  # After stopping display manager
```

### "vfio-pci: failed to bind" error

**Cause:** IOMMU groups incorrect, or device still in use.

**Check IOMMU groups:**
```bash
#!/usr/bin/env bash
for d in /sys/kernel/iommu_groups/*/devices/*; do
    n=${d#*/iommu_groups/*}; n=${n%%/*}
    printf 'IOMMU Group %s: ' "$n"
    lspci -nns "${d##*/}"
done | grep -E "VGA|Audio"
```

**Your GPU and its audio device should be in the same IOMMU group, OR in separate groups.**

If other devices (USB controllers, SATA controllers) are in the same group, you have two options:
1. Pass through ALL devices in that group to the VM
2. Enable ACS override patch (breaks IOMMU isolation - research first!)

### Black screen after VM shutdown

**Symptom:** VM shuts down, but Linux desktop doesn't return.

**Cause:** Stop hook script failed.

**Fix via SSH:**
```bash
ssh user@your-host-ip

# Check if VM is actually stopped
sudo virsh list --all

# Check logs to see what failed
sudo journalctl -t vm-gpu-stop -n 50

# Manually run the stop script
sudo /etc/libvirt/hooks/qemu.d/win11/release/end/stop.sh

# If that fails, manually restart display manager
sudo systemctl start sddm
```

**Fix via TTY:**
```
Ctrl+Alt+F2
login
gpu-recovery
Ctrl+Alt+F1
```

### nvidia module won't unload

**Symptom:** Hook script fails with "module nvidia is in use"

**Cause:** Application is using the GPU.

**Fix:**
```bash
# Check what's using nvidia
lsof /dev/nvidia*

# Common issues:
# - Docker containers using nvidia runtime
# - Persistent nvidia daemon
# - Background apps (Steam, Discord)

# Stop nvidia-persistenced if running
sudo systemctl stop nvidia-persistenced

# Kill processes using GPU
sudo fuser -k /dev/nvidia*

# Try unloading again
sudo modprobe -r nvidia_drm nvidia_modeset nvidia_uvm nvidia
```

### VM starts but no display output

**Symptom:** VM is running (virsh list shows it), but monitor shows "No Signal"

**Possible causes:**

1. **Windows hasn't installed GPU drivers yet**
   - First boot: Windows uses generic display driver
   - Install NVIDIA drivers in Windows
   - Reboot VM

2. **Monitor input not switching**
   - Manually switch monitor input to the correct port
   - Try a different cable (DisplayPort vs HDMI)

3. **GPU ROM issue**
   - Some GPUs need VBIOS ROM dumping
   - Add to VM XML:
   ```xml
   <hostdev mode='subsystem' type='pci' managed='yes'>
     <source>
       <address domain='0x0000' bus='0x01' slot='0x00' function='0x0'/>
     </source>
     <rom file='/path/to/vbios.rom'/>
   </hostdev>
   ```

### Check if passthrough is working

**From host (via SSH while VM is running):**
```bash
# GPU should be bound to vfio-pci
lspci -k -s 01:00.0  # Use your GPU address
# Should show: Kernel driver in use: vfio-pci

# VM should be using the GPU
sudo virsh dumpxml win11 | grep -A5 hostdev
```

**From Windows VM:**
1. Open Device Manager
2. Look for your GPU under "Display adapters"
3. Install NVIDIA drivers if not present

## Complete Directory Structure

```
/etc/libvirt/hooks/
├── qemu                          # Main hook dispatcher (executable)
└── qemu.d/
    └── win11/                    # Your VM name
        ├── prepare/
        │   └── begin/
        │       └── start.sh      # Runs before VM starts (executable)
        └── release/
            └── end/
                └── stop.sh       # Runs after VM stops (executable)
```

## AMD GPU Users

Replace nvidia modules with amdgpu:

In start.sh:
```bash
modprobe -r amdgpu
```

In stop.sh:
```bash
modprobe amdgpu
```

## Quick Reference

### System Workflow (No iGPU)

| Action | What Happens | What You See |
|--------|--------------|--------------|
| **Boot host** | Normal boot with nvidia driver | Linux desktop |
| **Start VM** | SDDM stops → nvidia unloads → vfio-pci binds → VM starts | Display goes BLACK → Windows boot screen |
| **VM running** | Host is headless, GPU in VM | Monitor shows Windows |
| **Stop VM** | vfio-pci unbinds → nvidia loads → SDDM starts | Brief black screen → Linux desktop |

### Where Is the GPU?

| Scenario | GPU Bound To | Host Display | Monitor Shows | Control Host Via |
|----------|--------------|--------------|---------------|------------------|
| Host booted | nvidia | ✅ Working | Linux desktop | Keyboard/mouse |
| VM starting | (transition) | ❌ BLACK | Nothing | Wait... |
| VM running | vfio-pci (VM) | ❌ No graphics | Windows VM | SSH only |
| VM stopping | (transition) | ❌ BLACK | Nothing | Wait... |
| VM stopped | nvidia | ✅ Working | Linux desktop | Keyboard/mouse |

## Emergency Recovery

If something goes wrong and your display doesn't return, you have several options:

### Method 1: SSH (Recommended)

From another device on the same network:

```bash
# SSH into your host
ssh user@192.168.1.100  # Use your actual IP

# Check if VM is running
sudo virsh list --all

# Force stop the VM
sudo virsh destroy win11  # Replace win11 with your VM name

# Run the recovery script
gpu-recovery

# Or manually run the stop script
sudo /etc/libvirt/hooks/qemu.d/win11/release/end/stop.sh

# Check what went wrong
sudo journalctl -t vm-gpu-start -t vm-gpu-stop -n 100
```

### Method 2: TTY Console

If you can't SSH but the system is responsive:

1. Press `Ctrl+Alt+F2` (or F3, F4, etc.) to switch to a TTY
2. Login with your username and password
3. Run: `gpu-recovery`
4. Press `Ctrl+Alt+F1` to return to graphical interface

### Method 3: Hard Reset (Last Resort)

If nothing else works:

1. Hold the power button for 10 seconds to force shutdown
2. Boot normally - the GPU will bind to nvidia driver as usual
3. Check logs after boot: `sudo journalctl -t vm-gpu-start -n 100`

### Viewing Logs

Check what went wrong:

```bash
# Hook script logs
sudo journalctl -t vm-gpu-start -t vm-gpu-stop -n 100

# Libvirt logs
sudo journalctl -u libvirtd -n 100

# VM-specific logs
sudo tail -f /var/log/libvirt/qemu/win11.log
```

### Common Issues and Fixes

**Display never comes back after VM shutdown:**
```bash
# Via SSH:
sudo systemctl start sddm  # Or your display manager
```

**VM fails to start, display is black:**
```bash
# Via SSH:
sudo virsh destroy win11
gpu-recovery
# Check logs to see what failed
sudo journalctl -t vm-gpu-start -n 50
```
