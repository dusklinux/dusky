# GPU Passthrough Implementation Summary

## What Has Been Completed ✓

### 1. System Verification ✓
- Confirmed i7-14700KF (NO integrated GPU)
- NVIDIA RTX 4090 at PCI 01:00.0
- IOMMU enabled and working
- SSH daemon active at 10.10.10.9
- SDDM display manager running

### 2. Hook Scripts with Safety Timeout ✓

Created and ready to install:
- `/home/coops/user_scripts_local/Single_GPU_KVM_PASSTHROUGH/libvirt-hooks/qemu` - Main dispatcher
- `/home/coops/user_scripts_local/Single_GPU_KVM_PASSTHROUGH/libvirt-hooks/win11/prepare/begin/start.sh` - Start hook
- `/home/coops/user_scripts_local/Single_GPU_KVM_PASSTHROUGH/libvirt-hooks/win11/release/end/stop.sh` - Stop hook

**Key Features:**
- ✅ Automatic VM shutdown after timeout (default: 5 minutes)
- ✅ Comprehensive error handling and recovery
- ✅ Detailed logging to journald
- ✅ Validation of PCI devices before operations
- ✅ Cleanup traps to prevent stuck states

### 3. Testing and Validation Scripts ✓

Created in `/home/coops/user_scripts_local/Single_GPU_KVM_PASSTHROUGH/`:
- `gpu-passthrough-preflight-check.sh` - System readiness check
- `test-gpu-bind-unbind.sh` - Test GPU binding without VM
- `install-gpu-passthrough-hooks.sh` - Install hooks to /etc/libvirt/
- `validate-gpu-passthrough-ready.sh` - Final readiness check

### 4. Documentation ✓

Created comprehensive guides:
- `GPU-PASSTHROUGH-TESTING-GUIDE.md` - Complete testing procedure
- `single-gpu-passthrough-guide.md` - Updated with corrections for no-iGPU systems
- `single-gpu-passthrough-audit.md` - Technical audit findings

---

## What You Need to Do Next

### Step 1: Test GPU Bind/Unbind (5 minutes)

This tests the GPU can be bound to vfio-pci WITHOUT starting a VM:

```bash
# Have SSH ready on phone/laptop first!
# ssh coops@10.10.10.9

cd ~/user_scripts_local/Single_GPU_KVM_PASSTHROUGH
sudo ./test-gpu-bind-unbind.sh 30
```

**What happens:**
- Your display will go BLACK for 30 seconds
- GPU binds to vfio-pci, then back to nvidia
- Display returns automatically

**Success = Ready to proceed. Failure = Check logs and troubleshoot.**

---

### Step 2: Install Hook Scripts (2 minutes)

```bash
cd ~/user_scripts_local/Single_GPU_KVM_PASSTHROUGH/Single_GPU_KVM_PASSTHROUGH
sudo ./install-gpu-passthrough-hooks.sh win11
sudo systemctl restart libvirtd
```

Verify:
```bash
ls -la /etc/libvirt/hooks/
ls -la /etc/libvirt/hooks/qemu.d/win11/
```

---

### Step 3: Create Windows VM (30-45 minutes)

Follow the documented procedure in:
```
~/Documents/pensive/linux/Important Notes/KVM/Windows/
+ MOC Windows Installation Through Virt Manager.md
```

**Key configuration:**
- Name: win11 (must match hook directory name!)
- Chipset: Q35
- Firmware: UEFI with Secure Boot
- CPU: host-passthrough
- Storage: VirtIO (with virtio-win ISO)
- Network: VirtIO
- TPM: 2.0 emulated
- Hyper-V enlightenments enabled

**Important:** Test the VM works BEFORE adding GPU passthrough!

---

### Step 4: Add GPU to VM XML (5 minutes)

Once Windows is installed and working:

```bash
sudo virsh edit win11
```

Add inside `<devices>`:

```xml
<!-- NVIDIA RTX 4090 -->
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

---

### Step 5: First GPU Passthrough Test (10 minutes)

**CRITICAL PREPARATION:**

1. **Open SSH on phone/laptop:**
   ```bash
   ssh coops@10.10.10.9
   ```

2. **Set safety timeout:**
   ```bash
   export GPU_PASSTHROUGH_TIMEOUT=5  # 5 minutes
   echo 'export GPU_PASSTHROUGH_TIMEOUT=5' >> ~/.bashrc
   ```

3. **Validate readiness:**
   ```bash
   ~/user_scripts_local/Single_GPU_KVM_PASSTHROUGH/validate-gpu-passthrough-ready.sh win11
   ```

4. **Start monitoring (in SSH session):**
   ```bash
   sudo journalctl -f -t vm-gpu-start -t vm-gpu-stop -t vm-gpu-timeout
   ```

5. **Start the VM:**
   ```bash
   virsh start win11
   ```

**What will happen:**

| Time | Event | What You See |
|------|-------|--------------|
| 0:00 | VM starts | Linux desktop |
| 0:05 | GPU unbinds | Display goes BLACK |
| 0:10 | VM boots | Physical monitor shows Windows |
| 0:15-5:00 | VM running | Windows on monitor, host is headless |
| 5:00 | Timeout | VM shuts down |
| 5:05 | GPU restores | Linux desktop returns |

**Success criteria:**
- ✅ Display goes black smoothly
- ✅ Windows appears on monitor
- ✅ VM is usable (install NVIDIA drivers in Windows if needed)
- ✅ After 5 minutes, VM shuts down automatically
- ✅ Linux desktop returns

**If something goes wrong:**
- Via SSH: `sudo virsh destroy win11` (force stop)
- Via SSH: `gpu-recovery` (restore GPU to host)
- Check logs: `sudo journalctl -t vm-gpu-start -n 50`

---

## Directory Structure Created

```
/home/coops/user_scripts_local/Single_GPU_KVM_PASSTHROUGH
├── gpu-passthrough-preflight-check.sh          ← System check
├── test-gpu-bind-unbind.sh                     ← Test without VM
├── install-gpu-passthrough-hooks.sh            ← Install hooks
├── validate-gpu-passthrough-ready.sh           ← Final check
├── GPU-PASSTHROUGH-TESTING-GUIDE.md            ← Complete guide
├── single-gpu-passthrough-guide.md             ← Updated guide
├── single-gpu-passthrough-audit.md             ← Audit report
└── libvirt-hooks/
    ├── qemu                                     ← Main dispatcher
    └── win11/
        ├── prepare/begin/start.sh               ← VM start hook
        └── release/end/stop.sh                  ← VM stop hook

After installation:
/etc/libvirt/hooks/                             ← Installed hooks
```

---

## Safety Features Summary

### Automatic Timeout
- Default: 5 minutes (adjustable)
- VM automatically shuts down after timeout
- Prevents being stuck with headless host
- Disable with: `export GPU_PASSTHROUGH_TIMEOUT=0`

### Error Handling
- Validates PCI devices exist
- Checks nvidia modules unload successfully
- Attempts to restore GPU if binding fails
- Restarts display manager on errors

### Logging
- All actions logged to journald
- View with: `sudo journalctl -t vm-gpu-start -t vm-gpu-stop`
- Includes timestamps and error messages
- Helps troubleshooting when things go wrong

### Recovery Options
1. Automatic cleanup on script errors
2. Manual recovery via SSH: `gpu-recovery`
3. Force stop VM: `virsh destroy win11`
4. TTY access: Ctrl+Alt+F2
5. Hard reboot (last resort)

---

## Configuration Summary

| Setting | Value |
|---------|-------|
| VM Name | win11 |
| GPU PCI | 0000:01:00.0 |
| Audio PCI | 0000:01:00.1 |
| Display Manager | sddm |
| SSH IP | 10.10.10.9 |
| Default Timeout | 5 minutes |
| Current GPU Driver | nvidia |

---

## Important Reminders

### ⚠️ Your System Has NO Integrated Graphics

When the VM runs:
- **Host becomes completely headless**
- **NO display output from host**
- **Monitor shows ONLY the VM**
- **Control host ONLY via SSH**

This is NOT like systems with iGPU where:
- Host keeps display on integrated graphics
- Looking Glass shows VM in a window
- Both host and VM have displays simultaneously

**Your workflow:**
1. Linux desktop visible
2. Start VM → display goes BLACK
3. Monitor shows Windows
4. Stop VM → Linux desktop returns

### 🔒 SSH is MANDATORY

You MUST have SSH access from another device:
- Phone with Termux
- Laptop on same network
- Another computer

Test SSH BEFORE attempting GPU passthrough!

### ⏱️ Safety Timeout is Your Friend

For testing, ALWAYS use a timeout:
```bash
export GPU_PASSTHROUGH_TIMEOUT=5  # 5 minutes
```

Once you're confident everything works, you can disable it:
```bash
export GPU_PASSTHROUGH_TIMEOUT=0  # No automatic shutdown
```

---

## Next Steps Checklist

- [ ] Run preflight check: `./gpu-passthrough-preflight-check.sh`
- [ ] Test GPU bind/unbind: `sudo ./test-gpu-bind-unbind.sh 30`
- [ ] Install hooks: `sudo ./install-gpu-passthrough-hooks.sh win11`
- [ ] Create Windows VM using virt-manager
- [ ] Test VM boots without GPU
- [ ] Add GPU to VM XML: `sudo virsh edit win11`
- [ ] Validate readiness: `./validate-gpu-passthrough-ready.sh win11`
- [ ] Set timeout: `export GPU_PASSTHROUGH_TIMEOUT=5`
- [ ] Have SSH ready on another device
- [ ] First test: `virsh start win11`
- [ ] Monitor via SSH: `sudo journalctl -f -t vm-gpu-start`
- [ ] Verify VM appears on monitor
- [ ] Wait for automatic shutdown (5 min)
- [ ] Verify Linux desktop returns
- [ ] Install NVIDIA drivers in Windows
- [ ] Test gaming/applications
- [ ] Adjust or disable timeout as needed

---

## Getting Help

### View Logs
```bash
# Start hook logs
sudo journalctl -t vm-gpu-start -n 50

# Stop hook logs
sudo journalctl -t vm-gpu-stop -n 50

# Timeout logs
sudo journalctl -t vm-gpu-timeout -n 50

# All GPU passthrough logs
sudo journalctl -b | grep -E "gpu|vfio|nvidia"

# VM console output
sudo tail -f /var/log/libvirt/qemu/win11.log
```

### Check Status
```bash
# Is VM running?
virsh list --all

# What driver is GPU using?
lspci -k -s 01:00.0 | grep "Kernel driver"

# Is SSH accessible?
systemctl status sshd

# What's the current timeout?
echo $GPU_PASSTHROUGH_TIMEOUT
```

### Common Issues
See `GPU-PASSTHROUGH-TESTING-GUIDE.md` Troubleshooting section

---

## Files Ready for Review

Before running any tests, you may want to review:

1. **Start hook:** `~/user_scripts_local/Single_GPU_KVM_PASSTHROUGH/libvirt-hooks/win11/prepare/begin/start.sh`
2. **Stop hook:** `~/user_scripts_local/Single_GPU_KVM_PASSTHROUGH/libvirt-hooks/win11/release/end/stop.sh`
3. **Testing guide:** `~/user_scripts_local/Single_GPU_KVM_PASSTHROUGH/GPU-PASSTHROUGH-TESTING-GUIDE.md`

All scripts include comprehensive comments explaining each step.

---

## Ready to Begin

When you're ready to start testing:

```bash
# Step 1: Preflight check
cd ~/user_scripts_local/Single_GPU_KVM_PASSTHROUGH
./gpu-passthrough-preflight-check.sh

# Step 2: Test bind/unbind (DISPLAY WILL GO BLACK FOR 30 SEC)
# Have SSH ready first!
sudo ./test-gpu-bind-unbind.sh 30

# If that works, proceed with VM creation and GPU passthrough!
```

Good luck! 🚀
