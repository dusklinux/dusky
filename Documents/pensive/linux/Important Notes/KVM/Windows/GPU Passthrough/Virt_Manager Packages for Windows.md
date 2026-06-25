# Arch Linux KVM/QEMU Setup: Installation & Permissions (Kernel 7.1+)

This guide outlines the process of setting up a Type-1 Hypervisor environment on Arch Linux using KVM (Kernel-based Virtual Machine). This is specifically tailored for running Windows guests with near-native performance utilizing the modern modular `libvirt` architecture and the native `nftables` network stack.

## 1. Package Installation

We need a specific suite of tools to handle the hypervisor (KVM), the emulator (QEMU), and the management interface (Virt-Manager).

> [!WARNING] The 2026 Firewall Shift
> 
> As of April 2026, Arch Linux made `nftables` the native default. The old `iptables-nft` translation package was renamed back to `iptables`. The command below strictly uses the modern standards required for Kernel 7.1+.

Run the following command in your terminal:

```
sudo pacman --needed -S qemu-full libvirt virt-install virt-manager virt-viewer dnsmasq iproute2 openbsd-netcat edk2-ovmf swtpm nftables iptables libosinfo
```

### 📦 Understanding the Packages

> [!INFO]- Package Breakdown (Expand to read)
> 
> - **`qemu-full`**: The core emulator performing the actual hardware translation for the Guest OS.
>     
> - **`libvirt`**: The virtualization API. In modern Arch, this provides **modular daemons** (like `virtqemud` and `virtnetworkd`) instead of the legacy monolithic `libvirtd`.
>     
> - **`virt-manager`**: The GUI frontend used to manage VMs.
>     
> - **`virt-install`**: A command-line tool to provision new VMs (used by the GUI in the background).
>     
> - **`virt-viewer`**: Utility for displaying the graphical screen of the VM via SPICE.
>     
> - **`dnsmasq`**: Required by libvirt to provide internet access (DNS/DHCP) to VMs via NAT.
>     
> - **`iproute2`**: Modern suite of utilities for IP networking and bridges (replaces the deprecated `bridge-utils`).
>     
> - **`openbsd-netcat`**: Allows for remote management of KVM over SSH.
>     
> - **`edk2-ovmf`**: The UEFI Firmware. **Essential** for modern Windows 11 setups requiring Secure Boot/UEFI.
>     
> - **`swtpm`**: Software TPM emulator. **Mandatory for Windows 11**, which requires a Trusted Platform Module.
>     
> - **`nftables` & `iptables`**: The modern firewall backend used for network address translation (NAT). The base `iptables` package securely routes legacy libvirt network calls through the native `nftables` engine.
>     
> - **`libosinfo`**: A database that allows `virt-manager` to automatically configure optimal defaults (like virtio drivers and RAM limits) when you select "Windows 11".
>     

## 2. Permission Configuration & Service Activation

To use `virt-manager` as your normal user without typing your root password constantly, we must add your user to the appropriate group and enable the correct modular sockets.

> [!DANGER] Stop! Do not edit `libvirtd.conf`
> 
> Older guides will tell you to edit `/etc/libvirt/libvirtd.conf`. **Do not do this.** The monolithic daemon is deprecated. We now configure the dedicated QEMU daemon (`virtqemud`).

### Step A: Add User to the Libvirt Group

First, add your current user to the `libvirt` group. On modern Arch, `polkit` is pulled in as a dependency and will automatically grant members of this group password-less access to the management daemon.

```bash
sudo usermod -aG libvirt $USER
```
or 
```bash
sudo usermod -aG libvirt,kvm,input "$(id -un)"
```

_(Note: You **must** log out and log back in, or reboot, for this group change to take effect)._

### Step B: Explicit Socket Permissions (Optional but Recommended)

While `polkit` handles permissions by default, setting explicit socket permissions in the modular daemon config ensures you never get locked out if polkit rules are overridden or fail on headless setups.

1. Open the modular configuration file:
    

```
sudo nvim /etc/libvirt/virtqemud.conf
```

2. Scroll to the bottom and paste the following block to explicitly grant socket permissions to your group:
    

```
unix_sock_group = "libvirt"
unix_sock_rw_perms = "0770"
```

3. Save and exit (`:wq`).
    

### Step C: Enable and Start the Modular Sockets

We **do not** enable a master `.service` anymore. Modern libvirt relies entirely on `systemd` socket activation. You only enable the sockets for the components you need (QEMU and Networking).

Run this command to enable and start the required modular sockets:

```
sudo systemctl enable --now virtqemud.socket virtnetworkd.socket
```

## 3. System Validation

Before opening Virt-Manager, let's verify that the host is correctly configured to utilize KVM hardware acceleration.

Run the built-in validation tool:

```
virt-host-validate
```

> [!CHECK] Expected Output
> 
> You should see `PASS` across the board for QEMU, particularly checking for `Hardware virtualization` and `/dev/kvm`. If you see `WARN` for IOMMU, that is fine unless you plan on doing PCI/GPU Passthrough later.

Your Arch Linux system is now fully modernized and ready to deploy high-performance Windows virtual machines!