#!/usr/bin/env bash
#
# Validate GPU Passthrough Readiness
# Checks if system is ready for first GPU passthrough test
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

ERRORS=0
WARNINGS=0
VM_NAME="${1:-win11}"

print_header "GPU Passthrough Readiness Check for VM: $VM_NAME"

# Check 1: Hooks installed
print_header "1. Checking Hook Scripts"
if [[ -x "/etc/libvirt/hooks/qemu" ]]; then
    print_ok "Main hook dispatcher installed"
else
    print_error "Main hook NOT installed"
    printf '   Run: sudo ~/user_scripts_local/Single_GPU_KVM_PASSTHROUGH/install-gpu-passthrough-hooks.sh %s\n' "$VM_NAME"
    ((ERRORS++))
fi

if [[ -x "/etc/libvirt/hooks/qemu.d/$VM_NAME/prepare/begin/start.sh" ]]; then
    print_ok "VM start hook installed"
else
    print_error "VM start hook NOT installed"
    ((ERRORS++))
fi

if [[ -x "/etc/libvirt/hooks/qemu.d/$VM_NAME/release/end/stop.sh" ]]; then
    print_ok "VM stop hook installed"
else
    print_error "VM stop hook NOT installed"
    ((ERRORS++))
fi

# Check 2: VM exists
print_header "2. Checking VM Configuration"
if virsh list --all 2>/dev/null | grep -q "$VM_NAME"; then
    print_ok "VM '$VM_NAME' exists"

    # Check if VM has GPU passthrough configured
    if virsh dumpxml "$VM_NAME" 2>/dev/null | grep -q "bus='0x01' slot='0x00'"; then
        print_ok "GPU passthrough configured in VM XML"
    else
        print_warn "GPU NOT configured in VM XML yet"
        printf '   Add GPU with: sudo virsh edit %s\n' "$VM_NAME"
        ((WARNINGS++))
    fi
else
    print_warn "VM '$VM_NAME' does not exist yet"
    printf '   Create VM using virt-manager first\n'
    ((WARNINGS++))
fi

# Check 3: libvirtd running
print_header "3. Checking Libvirt Service"
if systemctl is-active --quiet libvirtd; then
    print_ok "libvirtd is running"
else
    print_warn "libvirtd is not running"
    printf '   Start with: sudo systemctl start libvirtd\n'
    ((WARNINGS++))
fi

# Check 4: SSH accessible
print_header "4. Checking SSH Access"
if systemctl is-active --quiet sshd; then
    print_ok "SSH daemon is running"
    IP=$(ip -4 addr show | grep "inet " | grep -v 127.0.0.1 | head -1 | awk '{print $2}' | cut -d/ -f1)
    printf '   Test from another device: ssh %s@%s\n' "$USER" "$IP"
else
    print_error "SSH daemon is NOT running (CRITICAL!)"
    printf '   Enable: sudo systemctl enable --now sshd\n'
    ((ERRORS++))
fi

# Check 5: Timeout configured
print_header "5. Checking Safety Timeout"
if [[ -n "${GPU_PASSTHROUGH_TIMEOUT:-}" ]]; then
    print_ok "Safety timeout is set: $GPU_PASSTHROUGH_TIMEOUT minutes"
else
    print_warn "Safety timeout NOT set"
    printf '   Set with: export GPU_PASSTHROUGH_TIMEOUT=5\n'
    printf '   Add to ~/.bashrc for persistence\n'
    ((WARNINGS++))
fi

# Check 6: GPU status
print_header "6. Checking GPU Status"
DRIVER=$(lspci -k -s 01:00.0 | grep "Kernel driver in use" | cut -d: -f2 | xargs)
if [[ "$DRIVER" == "nvidia" ]]; then
    print_ok "GPU is using nvidia driver (correct for host)"
elif [[ "$DRIVER" == "vfio-pci" ]]; then
    print_warn "GPU is using vfio-pci (VM might be running or test in progress)"
else
    print_warn "GPU is using: $DRIVER"
fi

# Check 7: Required modules
print_header "7. Checking Kernel Modules"
if lsmod | grep -q "^kvm_intel"; then
    print_ok "kvm_intel loaded"
else
    print_error "kvm_intel NOT loaded"
    ((ERRORS++))
fi

if lsmod | grep -q "^nvidia "; then
    print_ok "nvidia loaded"
else
    print_warn "nvidia NOT loaded (might be OK if GPU is in vfio mode)"
fi

# Check 8: Test scripts available
print_header "8. Checking Test Scripts"
if [[ -x "$HOME/user_scripts_local/Single_GPU_KVM_PASSTHROUGH/test-gpu-bind-unbind.sh" ]]; then
    print_ok "Bind/unbind test script available"
else
    print_warn "Test script not found or not executable"
fi

if [[ -x "$HOME/user_scripts_local/Single_GPU_KVM_PASSTHROUGH/gpu-recovery" ]] || [[ -x "$HOME/.local/bin/gpu-recovery" ]]; then
    print_ok "GPU recovery script available"
else
    print_warn "Recovery script not found"
    printf '   Create with instructions from guide\n'
    ((WARNINGS++))
fi

# Summary
print_header "Readiness Summary"
if [[ $ERRORS -eq 0 && $WARNINGS -eq 0 ]]; then
    printf '%b✓ ALL CHECKS PASSED%b\n' "$GREEN" "$NC"
    printf '\nYour system is READY for GPU passthrough testing!\n\n'
    printf '%bRecommended first test:%b\n' "$BLUE" "$NC"
    printf '1. Have SSH ready: ssh %s@%s\n' "$USER" "$(ip -4 addr show | grep "inet " | grep -v 127.0.0.1 | head -1 | awk '{print $2}' | cut -d/ -f1)"
    printf '2. Set timeout: export GPU_PASSTHROUGH_TIMEOUT=5\n'
    printf '3. Start VM: virsh start %s\n' "$VM_NAME"
    printf '4. Monitor logs via SSH: sudo journalctl -f -t vm-gpu-start -t vm-gpu-stop\n'
    printf '5. Wait for automatic shutdown after 5 minutes\n'
    exit 0
elif [[ $ERRORS -eq 0 ]]; then
    printf '%b⚠ READY WITH WARNINGS%b (%d warning(s))\n' "$YELLOW" "$NC" "$WARNINGS"
    printf '\nYou can proceed, but review warnings above.\n'
    exit 0
else
    printf '%b✗ NOT READY%b (%d error(s), %d warning(s))\n' "$RED" "$NC" "$ERRORS" "$WARNINGS"
    printf '\nFix critical errors before testing!\n'
    exit 1
fi
