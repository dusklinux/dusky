#!/usr/bin/env bash
# ==============================================================================
# 010_kvm_host_setup.sh
# Purpose: Prepares an Arch Linux host for KVM/QEMU virtualization using modern
#          modular daemons and bleeding-edge dependencies.
# ==============================================================================
set -euo pipefail

RED='\033[1;31m'
GREEN='\033[1;32m'
CYAN='\033[1;36m'
NC='\033[0m'

log_info() { echo -e "${CYAN}[INFO]${NC} $1"; }
log_success() { echo -e "${GREEN}[SUCCESS]${NC} $1"; }
log_error() { echo -e "${RED}[ERROR]${NC} $1"; exit 1; }

# --- Sudo Validation & Keep-Alive ---
log_info "Validating administrative privileges..."
sudo -v || log_error "Sudo authentication failed. This script requires privileges."
while true; do sudo -n true; sleep 60; kill -0 "$$" || exit; done 2>/dev/null &

# 1. Hardware Verification (Strict VT-x / AMD-V check)
log_info "Verifying hardware virtualization support..."
if ! grep -E -q '(vmx|svm)' /proc/cpuinfo; then
    log_error "Hardware virtualization is disabled in BIOS/UEFI or not supported."
fi
log_success "Hardware virtualization enabled at CPU level."

# 2. Package Installation (Removed deprecated bridge-utils)
log_info "Installing KVM packages via pacman..."
sudo pacman -Syu --needed --noconfirm \
    qemu-full virt-manager libvirt dnsmasq iptables-nft edk2-ovmf \
    virt-viewer swtpm openbsd-netcat libosinfo

# 3. Modular Daemon Configuration (Added virtnodedevd and virtproxyd)
log_info "Enabling modern libvirt modular daemons..."
sudo systemctl mask libvirtd.socket libvirtd-ro.socket libvirtd-admin.socket libvirtd-tls.socket libvirtd-tcp.socket libvirtd.service || true
sudo systemctl enable --now virtqemud.socket virtnetworkd.socket virtstoraged.socket virtnodedevd.socket virtproxyd.socket virtsecretd.socket virtnwfilterd.socket
log_success "Modular daemons activated."

# 4. Network Configuration (virbr0)
log_info "Configuring default NAT network bridge (virbr0)..."
sudo virsh -c qemu:///system net-autostart default || log_info "Default network already set to autostart."
sudo virsh -c qemu:///system net-start default || log_info "Default network already running."
log_success "Network bridging configured."

# 5. User Permissions
log_info "Granting standard user ($USER) virtualization management permissions..."
# Restored 'disk' for raw block passthrough, kept 'render' for 3D acceleration.
sudo usermod -aG libvirt,kvm,input,disk,render "$USER"
log_success "Permissions granted. (Re-login required for group changes to fully propagate)."

echo -e "${GREEN}=== Host Virtualization Setup Complete ===${NC}"
