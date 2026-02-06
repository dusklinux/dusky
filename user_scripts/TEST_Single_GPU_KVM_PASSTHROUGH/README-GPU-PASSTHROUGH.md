# GPU Passthrough Scripts and Documentation

## Quick Start

```bash
cd ~/user_scripts_local/Single_GPU_KVM_PASSTHROUGH

# 1. Check system readiness
./gpu-passthrough-preflight-check.sh

# 2. Test GPU binding (display will go black for 30 seconds!)
#    Have SSH ready first: ssh coops@10.10.10.9
sudo ./test-gpu-bind-unbind.sh 30

# 3. If test passes, install hooks
sudo ./install-gpu-passthrough-hooks.sh win11

# 4. Create Windows VM (follow documentation)

# 5. Validate everything is ready
./validate-gpu-passthrough-ready.sh win11

# 6. Start testing with safety timeout
export GPU_PASSTHROUGH_TIMEOUT=5
virsh start win11
```

## Available Scripts

### System Checks
- `gpu-passthrough-preflight-check.sh` - Comprehensive system verification
- `validate-gpu-passthrough-ready.sh` - Final readiness check before testing

### Testing
- `test-gpu-bind-unbind.sh [seconds]` - Test GPU binding without starting VM
  - Example: `sudo ./test-gpu-bind-unbind.sh 30`
  - Display will go black for specified duration
  - GPU binds to vfio-pci then returns to nvidia

### Installation
- `install-gpu-passthrough-hooks.sh [vm-name]` - Install libvirt hooks
  - Example: `sudo ./install-gpu-passthrough-hooks.sh win11`
  - Copies hooks to /etc/libvirt/hooks/
  - Sets correct permissions

### Hook Scripts (in libvirt-hooks/)
- `qemu` - Main libvirt hook dispatcher
- `win11/prepare/begin/start.sh` - Runs before VM starts (unbind GPU from host)
- `win11/release/end/stop.sh` - Runs after VM stops (restore GPU to host)

### Documentation
- `IMPLEMENTATION-SUMMARY.md` - What has been done and next steps (READ THIS FIRST!)
- `GPU-PASSTHROUGH-TESTING-GUIDE.md` - Complete testing procedure with troubleshooting
- `single-gpu-passthrough-guide.md` - Full GPU passthrough guide (updated for no-iGPU)
- `single-gpu-passthrough-audit.md` - Technical audit findings
- `README-GPU-PASSTHROUGH.md` - This file

## Safety Features

### Automatic Timeout
Set before starting VM:
```bash
export GPU_PASSTHROUGH_TIMEOUT=5  # Minutes
```

VM will automatically shut down after timeout, restoring GPU to host.

### Recovery Commands
```bash
# Emergency recovery
gpu-recovery

# Force stop VM
sudo virsh destroy win11

# Manual GPU restore
sudo /etc/libvirt/hooks/qemu.d/win11/release/end/stop.sh

# Check logs
sudo journalctl -t vm-gpu-start -t vm-gpu-stop -n 50
```

## Important Notes

### ⚠️ Your CPU Has NO Integrated Graphics
- i7-14700KF has no iGPU
- When VM runs, host becomes HEADLESS
- Monitor shows ONLY the VM
- Control host via SSH only

### 🔒 SSH is MANDATORY
Test SSH access before any GPU passthrough:
```bash
ssh coops@10.10.10.9
```

### 📊 Monitoring
```bash
# Watch VM status
watch -n 2 'virsh list --all'

# Watch GPU driver
watch -n 2 'lspci -k -s 01:00.0 | grep "Kernel driver"'

# Follow logs
sudo journalctl -f -t vm-gpu-start -t vm-gpu-stop
```

## Workflow

**Normal Operation:**
1. Linux desktop (GPU using nvidia)
2. Start VM → Display goes BLACK
3. Monitor shows Windows (GPU using vfio-pci)
4. Stop VM → Linux desktop returns

**With Safety Timeout:**
1. Set: `export GPU_PASSTHROUGH_TIMEOUT=5`
2. Start VM
3. After 5 minutes, VM auto-shuts down
4. GPU automatically returns to host

## Configuration

Current system:
- VM Name: win11
- GPU: 0000:01:00.0 (NVIDIA RTX 4090)
- Audio: 0000:01:00.1
- Display Manager: sddm
- SSH IP: 10.10.10.9

Edit hook scripts if your configuration differs:
- `libvirt-hooks/win11/prepare/begin/start.sh` (lines 10-12)
- `libvirt-hooks/win11/release/end/stop.sh` (lines 10-12)

## Need Help?

1. **Read documentation:** `IMPLEMENTATION-SUMMARY.md` and `GPU-PASSTHROUGH-TESTING-GUIDE.md`
2. **Check logs:** `sudo journalctl -t vm-gpu-start -n 50`
3. **Test without VM:** `sudo ./test-gpu-bind-unbind.sh 30`
4. **Verify hooks:** `./validate-gpu-passthrough-ready.sh win11`

## Files Not To Delete

Keep these in ~/user_scripts_local/Single_GPU_KVM_PASSTHROUGH/:
- All .sh scripts (you might need them again)
- All .md documentation
- libvirt-hooks/ directory (source for reinstallation)
