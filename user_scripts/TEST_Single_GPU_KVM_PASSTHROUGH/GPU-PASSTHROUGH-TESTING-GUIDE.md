# GPU Passthrough Testing Guide with Safety Timeout

This guide will help you safely test GPU passthrough with automatic revert functionality.

## System Configuration

- **CPU:** Intel i7-14700KF (NO integrated graphics)
- **GPU:** NVIDIA RTX 4090 (01:00.0)
- **Audio:** NVIDIA Audio (01:00.1)
- **Display Manager:** SDDM
- **Local IP:** 10.10.10.9

## Safety Features

All scripts include:
1. ✅ **Automatic timeout** - VM shuts down after specified minutes
2. ✅ **Error handling** - Attempts to restore GPU if binding fails
3. ✅ **Comprehensive logging** - All actions logged to journald
4. ✅ **Cleanup traps** - Ensures restoration on script errors

## Testing Phases

### Phase 1: Pre-Flight Checks (5 minutes)

Run the system verification script:

```bash
cd ~/user_scripts_local/Single_GPU_KVM_PASSTHROUGH
./gpu-passthrough-preflight-check.sh
```

**Expected output:**
- ✅ IOMMU enabled
- ✅ GPU and Audio devices found
- ✅ SSH running
- ✅ virt-manager installed

**If any errors:** Fix them before proceeding.

---

### Phase 2: Test GPU Bind/Unbind (2 minutes)

This test verifies the GPU can be bound to vfio-pci and restored WITHOUT starting a VM.

**IMPORTANT:** Your display will go BLACK for 30 seconds during this test!

**Setup:**
1. Have SSH ready on phone/laptop: `ssh coops@10.10.10.9`
2. Save all work and close applications using GPU
3. Run the test:

```bash
cd ~/user_scripts_local/Single_GPU_KVM_PASSTHROUGH
sudo ./test-gpu-bind-unbind.sh 30  # 30 seconds test
```

**What happens:**
1. Display goes BLACK (SDDM stops, nvidia unloads)
2. GPU binds to vfio-pci
3. Waits 30 seconds
4. GPU returns to nvidia
5. Display returns (SDDM starts)

**Via SSH, monitor the test:**
```bash
# Watch logs in real-time
sudo journalctl -f | grep -E "gpu|nvidia|vfio"

# Check current GPU driver
watch -n 1 'lspci -k -s 01:00.0 | grep "Kernel driver"'
```

**Success criteria:**
- ✅ Display goes black, then returns after 30 seconds
- ✅ No error messages in logs
- ✅ Desktop fully functional after restoration

**If it fails:**
- Display doesn't return: Via SSH, run `sudo systemctl start sddm`
- Check logs: `sudo journalctl -t vm-gpu-start -t vm-gpu-stop -n 100`

---

### Phase 3: Install Hooks (1 minute)

Install the libvirt hooks for automatic GPU passthrough:

```bash
cd ~/user_scripts_local/Single_GPU_KVM_PASSTHROUGH
sudo ./install-gpu-passthrough-hooks.sh win11
sudo systemctl restart libvirtd
```

**Verify installation:**
```bash
ls -la /etc/libvirt/hooks/
ls -la /etc/libvirt/hooks/qemu.d/win11/
```

Should show:
```
/etc/libvirt/hooks/qemu (executable)
/etc/libvirt/hooks/qemu.d/win11/prepare/begin/start.sh (executable)
/etc/libvirt/hooks/qemu.d/win11/release/end/stop.sh (executable)
```

---

### Phase 4: Create Windows VM (30-45 minutes)

Follow the documentation in:
```
/home/coops/git/dusky/Documents/pensive/linux/Important Notes/KVM/Windows/
```

**Key steps:**
1. Launch virt-manager
2. Create new VM with Windows 11 ISO
3. Configure: Q35 chipset, UEFI firmware, TPM 2.0
4. Set CPU to host-passthrough
5. Use VirtIO for storage and network
6. Attach virtio-win ISO
7. Install Windows

**DO NOT add GPU to VM XML yet!** First verify Windows boots without GPU passthrough.

---

### Phase 5: Add GPU to VM (5 minutes)

Once Windows is installed and working with QXL display:

```bash
# Edit VM configuration
sudo virsh edit win11
```

Add inside `<devices>` section (BEFORE the closing `</devices>` tag):

```xml
<!-- NVIDIA RTX 4090 GPU -->
<hostdev mode='subsystem' type='pci' managed='yes'>
  <source>
    <address domain='0x0000' bus='0x01' slot='0x00' function='0x0'/>
  </source>
</hostdev>

<!-- NVIDIA Audio -->
<hostdev mode='subsystem' type='pci' managed='yes'>
  <source>
    <address domain='0x0000' bus='0x01' slot='0x00' function='0x1'/>
  </source>
</hostdev>
```

Save and exit (`:wq` in vi).

**Verify XML:**
```bash
sudo virsh dumpxml win11 | grep -A5 hostdev
```

---

### Phase 6: First GPU Passthrough Test (10 minutes)

**CRITICAL SETUP:**

1. **Open SSH session from another device:**
   ```bash
   ssh coops@10.10.10.9
   ```

2. **Set safety timeout (5 minutes for first test):**
   ```bash
   export GPU_PASSTHROUGH_TIMEOUT=5
   echo "export GPU_PASSTHROUGH_TIMEOUT=5" >> ~/.bashrc
   ```

3. **Verify timeout is set:**
   ```bash
   echo $GPU_PASSTHROUGH_TIMEOUT  # Should show: 5
   ```

4. **Start monitoring logs (in SSH session):**
   ```bash
   sudo journalctl -f -t vm-gpu-start -t vm-gpu-stop -t vm-gpu-timeout
   ```

5. **From host desktop, start the VM:**
   ```bash
   virsh start win11
   # OR use virt-manager GUI
   ```

**What happens:**

```
Time    Event
----    -----
0:00    Click "Start" in virt-manager
0:02    Display goes BLACK (host loses graphics)
0:05    Hook script completes
0:10    Windows should appear on physical monitor
0:15    Use Windows normally
5:00    VM automatically shuts down (safety timeout)
5:05    Display returns to Linux desktop
```

**Via SSH, monitor status:**
```bash
# Check if VM is running
watch -n 2 'virsh list --all'

# Check GPU driver
watch -n 2 'lspci -k -s 01:00.0 | grep "Kernel driver"'

# Should show: vfio-pci when VM is running
#              nvidia when VM is stopped
```

**Success criteria:**
- ✅ Linux display goes black within 5 seconds
- ✅ Physical monitor shows Windows boot within 60 seconds
- ✅ Windows is usable with GPU
- ✅ After 5 minutes, VM shuts down automatically
- ✅ Linux desktop returns within 10 seconds

**If something goes wrong:**

Via SSH:
```bash
# Force stop VM
sudo virsh destroy win11

# Run recovery
gpu-recovery

# Or manually restore
sudo /etc/libvirt/hooks/qemu.d/win11/release/end/stop.sh

# Check logs for errors
sudo journalctl -t vm-gpu-start -t vm-gpu-stop -n 100
```

---

## Phase 7: Extended Testing (Optional)

Once the 5-minute test works perfectly, try longer durations:

```bash
# 15 minute test
export GPU_PASSTHROUGH_TIMEOUT=15
virsh start win11

# 30 minute test
export GPU_PASSTHROUGH_TIMEOUT=30
virsh start win11

# Disable timeout (use manually)
export GPU_PASSTHROUGH_TIMEOUT=0
virsh start win11
# You must manually shut down Windows or run: virsh shutdown win11
```

---

## Troubleshooting

### Display never returns after VM shuts down

**Via SSH:**
```bash
# Check if VM is actually stopped
virsh list --all

# Check GPU driver
lspci -k -s 01:00.0 | grep "Kernel driver"

# If still vfio-pci, manually run stop script
sudo /etc/libvirt/hooks/qemu.d/win11/release/end/stop.sh

# Force restart display manager
sudo systemctl restart sddm
```

### VM doesn't show on monitor

**Possible causes:**
1. Windows hasn't installed NVIDIA drivers yet (first boot)
2. Monitor input not switched correctly
3. GPU ROM issue (rare)

**Check via SSH:**
```bash
# Verify GPU is in VM
sudo virsh dumpxml win11 | grep -A5 hostdev

# Check VM is actually running
virsh list --all

# Check GPU driver
lspci -k -s 01:00.0  # Should show: vfio-pci
```

### nvidia module won't unload

**Via SSH:**
```bash
# Check what's using nvidia
sudo lsof /dev/nvidia*

# Common culprits:
# - Docker containers with nvidia runtime
# - Wayland compositor
# - Steam, Discord with hardware acceleration

# Stop those services first, then retry
```

### VM crashes or freezes

**Via SSH:**
```bash
# Check VM logs
sudo tail -f /var/log/libvirt/qemu/win11.log

# Force destroy VM
sudo virsh destroy win11

# GPU should auto-restore via stop hook
# If not, manually run:
gpu-recovery
```

---

## Log Locations

```bash
# Hook execution logs
sudo journalctl -t vm-gpu-start
sudo journalctl -t vm-gpu-stop
sudo journalctl -t vm-gpu-timeout

# Libvirt logs
sudo journalctl -u libvirtd

# VM console output
sudo tail -f /var/log/libvirt/qemu/win11.log

# All GPU-related logs from last boot
sudo journalctl -b -t vm-gpu-start -t vm-gpu-stop -t vm-gpu-timeout
```

---

## Quick Reference Commands

```bash
# Start VM with timeout
export GPU_PASSTHROUGH_TIMEOUT=5
virsh start win11

# Stop VM manually
virsh shutdown win11        # Graceful shutdown
virsh destroy win11         # Force stop

# Check VM status
virsh list --all

# Check GPU driver
lspci -k -s 01:00.0 | grep "Kernel driver"

# Emergency recovery
gpu-recovery

# Watch logs
sudo journalctl -f -t vm-gpu-start -t vm-gpu-stop

# Test bind/unbind without VM
sudo ~/user_scripts_local/Single_GPU_KVM_PASSTHROUGH/test-gpu-bind-unbind.sh 30
```

---

## Safety Checklist

Before EVERY test:
- [ ] SSH accessible from another device
- [ ] Know your IP address (10.10.10.9)
- [ ] Timeout is set (`echo $GPU_PASSTHROUGH_TIMEOUT`)
- [ ] All work saved
- [ ] No other applications using GPU
- [ ] Have phone/laptop ready for SSH

---

## Next Steps After Successful Testing

1. **Install NVIDIA drivers in Windows VM**
2. **Test gaming/applications**
3. **Adjust timeout or disable it**
4. **Consider network streaming (Sunshine/Moonlight) if you want to view VM from other devices**
5. **Set up shared folders between host and VM**

---

## Removing GPU Passthrough

If you want to go back to standard VM without GPU:

```bash
# Edit VM
sudo virsh edit win11

# Remove the <hostdev> blocks for GPU and Audio
# Save and exit

# Delete hooks (optional)
sudo rm -rf /etc/libvirt/hooks/qemu.d/win11
sudo systemctl restart libvirtd
```

The GPU will stay with the host (nvidia driver) all the time.
