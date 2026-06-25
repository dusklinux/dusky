# GPU Passthrough Boot Recovery Walkthrough

If you configured GPU passthrough and can no longer boot (black screen, no display), this guide will help you recover.

## The Problem

GPU passthrough isolates a GPU from the host so a VM can use it. If you isolate your ONLY GPU, Linux boots but has no display output because:

1. `vfio-pci` driver binds to your GPU early in boot (via initramfs)
2. The nvidia/amdgpu driver never gets to load
3. Your display manager starts but has no GPU to render to

## Prerequisites

- Live USB of your distro (or any Arch-based live USB for Arch systems)
- Know your root partition device (e.g., `/dev/nvme0n1p2`, `/dev/sda2`)

## Step 1: Boot Live USB

Boot from your live USB and open a terminal.

## Step 2: Mount Your Root Filesystem

### Standard ext4/xfs:
```bash
sudo mount /dev/nvme0n1p2 /mnt # Make sure you change nvme0n1p2 to your hdd or boot drive
```

### Btrfs with subvolumes:
```bash
# First, mount the btrfs root to see subvolumes
sudo mount /dev/nvme0n1p2 /mnt
ls /mnt  # Look for @ or similar subvolume names

# If using @ as root subvolume:
sudo umount /mnt
sudo mount -o subvol=@ /dev/nvme0n1p2 /mnt
```

## Step 3: Identify the Problem

Check for vfio configuration:

```bash
# Check modprobe config
cat /mnt/etc/modprobe.d/vfio.conf
# Example output: options vfio-pci ids=10de:2684,10de:22ba

# Check mkinitcpio modules
grep '^MODULES=' /mnt/etc/mkinitcpio.conf
# Problematic: MODULES=(vfio_pci vfio_iommu_type1 vfio)

# Check kernel parameters
grep 'GRUB_CMDLINE_LINUX' /mnt/etc/default/grub
# Look for: intel_iommu=on iommu=pt vfio-pci.ids=...
```

## Step 4: Remove GPU Isolation

### Option A: Remove all passthrough config (recommended if you have one GPU)

```bash
# Remove vfio modprobe config
sudo rm /mnt/etc/modprobe.d/vfio.conf

# Clear MODULES in mkinitcpio.conf
sudo sed -i 's/^MODULES=(.*/MODULES=()/' /mnt/etc/mkinitcpio.conf

# Verify
grep '^MODULES=' /mnt/etc/mkinitcpio.conf
# Should show: MODULES=()
```

### Option B: Keep passthrough but fix device IDs (if you have 2 GPUs)

Edit `/mnt/etc/modprobe.d/vfio.conf` and remove your primary GPU's IDs, keeping only the GPU you want to pass through.

```bash
# Find your GPU IDs
lspci -nn | grep -i vga

# Edit to only include the passthrough GPU
sudo nano /mnt/etc/modprobe.d/vfio.conf
```

## Step 5: Mount for Chroot

### Standard filesystem:
```bash
sudo mount /dev/nvme0n1p1 /mnt/boot/efi  # or /mnt/boot
sudo mount --bind /dev /mnt/dev
sudo mount --bind /proc /mnt/proc
sudo mount --bind /sys /mnt/sys
sudo mount --bind /run /mnt/run
```

### Btrfs with subvolumes:
```bash
# Mount all subvolumes (adjust names as needed)
sudo mount -o subvol=@home /dev/nvme0n1p2 /mnt/home
sudo mount -o subvol=@cache /dev/nvme0n1p2 /mnt/var/cache
sudo mount -o subvol=@log /dev/nvme0n1p2 /mnt/var/log
sudo mount /dev/nvme0n1p1 /mnt/boot/efi
```

## Step 6: Regenerate Initramfs

### Arch/CachyOS/Manjaro (using arch-chroot):
```bash
sudo arch-chroot /mnt mkinitcpio -P
```

### Manual chroot:
```bash
sudo chroot /mnt /bin/bash
mkinitcpio -P
exit
```

### Fedora/RHEL:
```bash
sudo chroot /mnt /bin/bash
dracut --force
exit
```

### Ubuntu/Debian:
```bash
sudo chroot /mnt /bin/bash
update-initramfs -u -k all
exit
```

## Step 7: Regenerate Bootloader Config

### GRUB (Arch):
```bash
sudo arch-chroot /mnt grub-mkconfig -o /boot/grub/grub.cfg
```

### GRUB (Ubuntu/Fedora):
```bash
sudo chroot /mnt grub2-mkconfig -o /boot/grub2/grub.cfg
```

### systemd-boot:
No regeneration needed unless you modified kernel parameters in the entry files.

## Step 8: Cleanup and Reboot

```bash
sudo umount -R /mnt
reboot
```

## Troubleshooting

### "failed to detect root filesystem" during mkinitcpio
This usually means /dev isn't mounted properly. Use `arch-chroot` which handles this automatically, or ensure devtmpfs is mounted:
```bash
sudo mount -t devtmpfs devtmpfs /mnt/dev
```

### nvidia module not found errors
If you see warnings about nvidia modules not found for some kernels, that kernel may not have nvidia-dkms modules built. Boot a kernel that shows successful build.

### Still no display after fix
1. Check if nouveau is blacklisted: `cat /mnt/etc/modprobe.d/*nouveau*`
2. Ensure nvidia driver is installed: `arch-chroot /mnt pacman -Q nvidia`
3. Try booting with `nomodeset` kernel parameter temporarily

## Prevention: Proper GPU Passthrough Setup

To do GPU passthrough correctly with a single GPU:

1. **Use Looking Glass** - Lets you see VM display through host
2. **Use SSH/VNC** - Access host headlessly
3. **Get a second GPU** - Even a cheap GT 710 works for host display
4. **Use integrated graphics** - If your CPU has an iGPU, use that for host

## Files Reference

| File | Purpose |
|------|---------|
| `/etc/modprobe.d/vfio.conf` | Binds specific PCI devices to vfio-pci |
| `/etc/mkinitcpio.conf` | Controls which modules load in initramfs |
| `/etc/default/grub` | Kernel boot parameters |
| `/etc/dracut.conf.d/*.conf` | Dracut initramfs config (Fedora) |

## Quick Recovery Cheatsheet

```bash
# Mount
sudo mount -o subvol=@ /dev/nvme0n1p2 /mnt

# Fix
sudo rm /mnt/etc/modprobe.d/vfio.conf
sudo sed -i 's/^MODULES=(.*/MODULES=()/' /mnt/etc/mkinitcpio.conf

# Mount rest and rebuild
sudo mount /dev/nvme0n1p1 /mnt/boot/efi
sudo arch-chroot /mnt mkinitcpio -P
sudo arch-chroot /mnt grub-mkconfig -o /boot/grub/grub.cfg

# Reboot
sudo umount -R /mnt
reboot
```
