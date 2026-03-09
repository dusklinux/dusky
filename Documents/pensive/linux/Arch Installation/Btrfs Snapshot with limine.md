# BTRFS Snapshots with Snapper & Limine on Encrypted Arch Linux

## Table of Contents

- [Overview](#overview)
- [Prerequisites](#prerequisites)
- [Part 1 — Install & Configure the Limine Bootloader](#part-1--install--configure-the-limine-bootloader)
- [Part 2 — Create Dedicated BTRFS Snapshot Subvolumes](#part-2--create-dedicated-btrfs-snapshot-subvolumes)
- [Part 3 — Install & Configure Snapper](#part-3--install--configure-snapper)
- [Part 4 — Limine–Snapper Integration Packages](#part-4--liminesnapper-integration-packages)
- [Part 5 — Configure mkinitcpio for Encrypted BTRFS](#part-5--configure-mkinitcpio-for-encrypted-btrfs)
- [Part 6 — Configure limine-update & Finalize Boot](#part-6--configure-limine-update--finalize-boot)
- [Part 7 — Enable Services](#part-7--enable-services)
- [Part 8 — Usage: Creating & Managing Snapshots](#part-8--usage-creating--managing-snapshots)
- [Part 9 — Restoring / Rolling Back from Snapshots](#part-9--restoring--rolling-back-from-snapshots)
- [Appendix A — Writing Custom mkinitcpio Hooks](#appendix-a--writing-custom-mkinitcpio-hooks)
- [Appendix B — Troubleshooting](#appendix-b--troubleshooting)

---

## Overview

### What We Are Building

```
┌─────────────────────────────────────────────────────┐
│  ESP (FAT32) — mounted at /boot                    │
│  ├── EFI/BOOT/BOOTX64.EFI  (Limine)                │
│  ├── limine.conf                                    │
│  ├── vmlinuz-linux                                  │
│  └── initramfs-linux.img                            │
├─────────────────────────────────────────────────────┤
│  LUKS Encrypted Partition                           │
│  └── BTRFS Filesystem (/dev/mapper/cryptroot)       │
│      ├── @               → mounted at /             │
│      ├── @home           → mounted at /home         │
│      ├── @snapshots      → mounted at /.snapshots   │
│      ├── @home_snapshots → mounted at /home/.snapshots│
│      ├── @var_log        → mounted at /var/log      │
│      └── @var_cache      → mounted at /var/cache    │
└─────────────────────────────────────────────────────┘
```

**Key components:**

| Component | Purpose |
|---|---|
| **Limine** | Modern, lightweight bootloader |
| **Snapper** | BTRFS snapshot manager (create, list, delete, rollback) |
| **limine-mkinitcpio-hook** | Auto-regenerates `limine.conf` on kernel updates |
| **limine-snapper-sync** | Syncs kernel/initramfs into snapshots for bootable rollbacks |
| **btrfs-overlayfs** | mkinitcpio hook allowing boot into read-only snapshots via overlayfs |

---

## Prerequisites

> [!IMPORTANT]
> **Have a live USB ready.** Switching bootloaders and modifying initramfs can make your system unbootable if something goes wrong. Always have a recovery path.

- Arch Linux installed on a BTRFS filesystem
- LUKS encryption on the root partition
- An EFI System Partition (ESP) — this guide assumes UEFI boot
- An AUR helper installed (`paru` or `yay`)

### Gather Your System Info

Run these commands and **save the output** — you'll need these values throughout the guide:

```bash
# 1. Identify your disk layout
lsblk -f
```

```bash
# 2. Get the UUID of your LUKS partition (the encrypted device, e.g., /dev/nvme0n1p2)
#    Look for TYPE="crypto_LUKS"
sudo blkid /dev/nvme0n1p2
```

```bash
# 3. Get the UUID of the decrypted BTRFS filesystem
sudo blkid /dev/mapper/cryptroot
```

```bash
# 4. Check your current BTRFS subvolume layout
sudo btrfs subvolume list /
```

```bash
# 5. Check current mount points
findmnt -t btrfs
```

```bash
# 6. Identify your ESP partition (e.g., /dev/nvme0n1p1)
findmnt /boot
```

```bash
# 7. Check if you're booting in UEFI mode
ls /sys/firmware/efi
# If this directory exists, you're on UEFI
```

> [!NOTE]
> Throughout this guide, I will use these placeholders. **Replace them with your actual values:**
> - `LUKS-UUID` → UUID of the encrypted partition (e.g., `a1b2c3d4-...`)
> - `BTRFS-UUID` → UUID of the decrypted BTRFS filesystem
> - `/dev/nvme0n1p1` → your ESP partition
> - `/dev/nvme0n1p2` → your LUKS partition
> - `/dev/mapper/cryptroot` → your dm-crypt device name

---

## Part 1 — Install & Configure the Limine Bootloader

### 1.1 Install the Limine Package

```bash
sudo pacman -S limine efibootmgr
```

### 1.2 Deploy Limine to the ESP

```bash
# Create directory for Limine on the ESP
sudo mkdir -p /boot/EFI/BOOT

# Copy the Limine EFI binary
sudo cp /usr/share/limine/BOOTX64.EFI /boot/EFI/BOOT/BOOTX64.EFI
```

> [!TIP]
> Placing Limine at `EFI/BOOT/BOOTX64.EFI` makes it the UEFI fallback bootloader. If you want to keep your current bootloader as fallback, place Limine at a different path instead:
> ```bash
> sudo mkdir -p /boot/EFI/limine
> sudo cp /usr/share/limine/BOOTX64.EFI /boot/EFI/limine/BOOTX64.EFI
> ```

### 1.3 Create a UEFI Boot Entry

```bash
# Find your ESP disk and partition number
# e.g., if ESP is /dev/nvme0n1p1 → disk is /dev/nvme0n1, part is 1

sudo efibootmgr --create \
  --disk /dev/nvme0n1 \
  --part 1 \
  --loader '\EFI\BOOT\BOOTX64.EFI' \
  --label 'Limine' \
  --unicode
```

```bash
# Verify the entry was created and check boot order
efibootmgr -v
```

```bash
# Set Limine as the first boot option (replace XXXX with the boot number)
sudo efibootmgr --bootorder XXXX,$(efibootmgr | grep BootOrder | sed 's/BootOrder: //')
```

### 1.4 Create limine.conf

> [!WARNING]
> Get your kernel command line right. If `cryptdevice` is wrong, you will not be able to boot. Double-check your LUKS UUID.

```bash
# Get your current kernel cmdline for reference (if migrating from another bootloader)
cat /proc/cmdline
```

Create the Limine configuration file:

```bash
sudo tee /boot/limine.conf << 'LIMINE_EOF'
timeout: 5
verbose: no

/Arch Linux
    protocol: linux
    kernel_path: boot():/vmlinuz-linux
    cmdline: cryptdevice=UUID=LUKS-UUID:cryptroot root=/dev/mapper/cryptroot rootflags=subvol=@ rw quiet
    module_path: boot():/initramfs-linux.img

/Arch Linux (Fallback)
    protocol: linux
    kernel_path: boot():/vmlinuz-linux
    cmdline: cryptdevice=UUID=LUKS-UUID:cryptroot root=/dev/mapper/cryptroot rootflags=subvol=@ rw
    module_path: boot():/initramfs-linux-fallback.img
LIMINE_EOF
```

Now **edit the file** and replace `LUKS-UUID` with your actual UUID:

```bash
# Get LUKS UUID and substitute it in
LUKS_UUID=$(sudo blkid -s UUID -o value /dev/nvme0n1p2)
sudo sed -i "s/LUKS-UUID/$LUKS_UUID/g" /boot/limine.conf
```

```bash
# Verify the config looks correct
cat /boot/limine.conf
```

> [!NOTE]
> **About microcode:** If you have the `microcode` hook in your mkinitcpio HOOKS (which we set up in Part 5), microcode is bundled into the initramfs and you do NOT need a separate `module_path` for it.
>
> If you prefer **early loading** (separate microcode image), add it as the first module:
> ```
> module_path: boot():/intel-ucode.img
> module_path: boot():/initramfs-linux.img
> ```
> Replace `intel-ucode.img` with `amd-ucode.img` for AMD CPUs.

### 1.5 Test the Boot

> [!IMPORTANT]
> **Before rebooting**, make sure you have your live USB ready. If Limine doesn't work, you can boot the live USB, mount your partitions, and fix the config.

```bash
# Reboot and select Limine from the UEFI boot menu (press F12/F2/Del during POST)
reboot
```

If Limine boots successfully, proceed. If not, boot from your previous bootloader or live USB and fix `/boot/limine.conf`.

---

## Part 2 — Create Dedicated BTRFS Snapshot Subvolumes

The goal: store snapshot metadata in **separate top-level subvolumes** so they survive rollbacks.

### 2.1 Check Existing Subvolumes

```bash
sudo btrfs subvolume list /
```

You should already have at least `@` (root) and likely `@home`. If you don't have `@var_log` and `@var_cache`, create them too (they prevent log/cache data from being included in snapshots).

### 2.2 Mount the Top-Level BTRFS Volume

```bash
sudo mkdir -p /mnt/btrfs-root
sudo mount -o subvolid=5 /dev/mapper/cryptroot /mnt/btrfs-root
```

### 2.3 Create the Snapshot Subvolumes

```bash
# Snapshot subvolume for root (/)
sudo btrfs subvolume create /mnt/btrfs-root/@snapshots

# Snapshot subvolume for home (/home)
sudo btrfs subvolume create /mnt/btrfs-root/@home_snapshots
```

If you don't already have these, create them too:

```bash
# Optional: separate subvolumes for var/log and var/cache
sudo btrfs subvolume create /mnt/btrfs-root/@var_log
sudo btrfs subvolume create /mnt/btrfs-root/@var_cache
```

### 2.4 Verify

```bash
sudo btrfs subvolume list /mnt/btrfs-root
```

You should see something like:

```
ID 256 gen ... top level 5 path @
ID 257 gen ... top level 5 path @home
ID 258 gen ... top level 5 path @snapshots
ID 259 gen ... top level 5 path @home_snapshots
ID 260 gen ... top level 5 path @var_log
ID 261 gen ... top level 5 path @var_cache
```

### 2.5 Unmount Top-Level

```bash
sudo umount /mnt/btrfs-root
```

### 2.6 Create Mount Points

```bash
sudo mkdir -p /.snapshots
sudo mkdir -p /home/.snapshots
```

If you created `@var_log` and `@var_cache` and they aren't already mounted:

```bash
sudo mkdir -p /var/log
sudo mkdir -p /var/cache
```

### 2.7 Update /etc/fstab

```bash
# Back up fstab first!
sudo cp /etc/fstab /etc/fstab.bak
```

```bash
# Get your BTRFS UUID
sudo blkid -s UUID -o value /dev/mapper/cryptroot
```

Add these entries to `/etc/fstab`. Match the mount options to your existing BTRFS entries:

```bash
sudo tee -a /etc/fstab << 'EOF'

# BTRFS Snapshot Subvolumes
UUID=BTRFS-UUID  /.snapshots       btrfs  subvol=@snapshots,noatime,compress=zstd,space_cache=v2       0 0
UUID=BTRFS-UUID  /home/.snapshots  btrfs  subvol=@home_snapshots,noatime,compress=zstd,space_cache=v2  0 0
EOF
```

> [!IMPORTANT]
> Replace `BTRFS-UUID` with your actual BTRFS filesystem UUID. Make sure the mount options (`noatime,compress=zstd,space_cache=v2`) match what you use for your other BTRFS subvolumes. Check your existing fstab entries.

```bash
# Substitute the actual UUID
BTRFS_UUID=$(sudo blkid -s UUID -o value /dev/mapper/cryptroot)
sudo sed -i "s/BTRFS-UUID/$BTRFS_UUID/g" /etc/fstab
```

If you also created `@var_log` and `@var_cache` and they're not in fstab yet:

```bash
sudo tee -a /etc/fstab << 'EOF'
UUID=BTRFS-UUID  /var/log    btrfs  subvol=@var_log,noatime,compress=zstd,space_cache=v2    0 0
UUID=BTRFS-UUID  /var/cache  btrfs  subvol=@var_cache,noatime,compress=zstd,space_cache=v2  0 0
EOF
```

### 2.8 Mount Everything

```bash
sudo mount -a
```

```bash
# Verify all mounts
findmnt -t btrfs
```

You should see `/.snapshots` and `/home/.snapshots` mounted with their respective subvolumes.

---

## Part 3 — Install & Configure Snapper

### 3.1 Install Snapper

```bash
sudo pacman -S snapper
```

Optional but recommended — automatic pre/post snapshots on every pacman transaction:

```bash
sudo pacman -S snap-pac
```

### 3.2 Create Snapper Configurations

```bash
# Create config for root filesystem
sudo snapper -c root create-config /

# Create config for home
sudo snapper -c home create-config /home
```

### 3.3 Fix the Snapshot Subvolume Mapping

> [!WARNING]
> This is the critical step. When Snapper runs `create-config`, it creates its own `.snapshots` subvolume **nested inside** the target subvolume. We need to delete those and use our dedicated top-level subvolumes instead.

```bash
# Delete the subvolumes Snapper auto-created (they're nested inside @ and @home)
sudo btrfs subvolume delete /.snapshots
sudo btrfs subvolume delete /home/.snapshots
```

```bash
# Recreate the mount point directories
sudo mkdir -p /.snapshots
sudo mkdir -p /home/.snapshots
```

```bash
# Remount our dedicated subvolumes
sudo mount -a
```

```bash
# Set correct permissions
sudo chmod 750 /.snapshots
sudo chmod 750 /home/.snapshots
```

### 3.4 Verify Snapper Sees the Configs

```bash
sudo snapper list-configs
```

Expected output:

```
Config │ Subvolume
───────┼──────────
home   │ /home
root   │ /
```

### 3.5 Enable BTRFS Quotas

Quotas let Snapper use space-aware cleanup algorithms (`SPACE_LIMIT`, `FREE_LIMIT`):

```bash
sudo btrfs quota enable /
```

> [!NOTE]
> BTRFS quotas can cause minor performance overhead on write-heavy workloads. If you experience issues, you can disable them with `sudo btrfs quota disable /` and rely only on the number-based cleanup limits instead.

### 3.6 Tune Snapper Settings

Edit both configs to set sensible defaults:

```bash
# Disable automatic timeline snapshots (we'll use manual/pacman-triggered snapshots)
sudo sed -i 's/^TIMELINE_CREATE="yes"/TIMELINE_CREATE="no"/' /etc/snapper/configs/root
sudo sed -i 's/^TIMELINE_CREATE="yes"/TIMELINE_CREATE="no"/' /etc/snapper/configs/home

# Limit number of regular snapshots kept
sudo sed -i 's/^NUMBER_LIMIT="50"/NUMBER_LIMIT="5"/' /etc/snapper/configs/root
sudo sed -i 's/^NUMBER_LIMIT="50"/NUMBER_LIMIT="5"/' /etc/snapper/configs/home

# Limit number of important snapshots kept
sudo sed -i 's/^NUMBER_LIMIT_IMPORTANT="10"/NUMBER_LIMIT_IMPORTANT="5"/' /etc/snapper/configs/root
sudo sed -i 's/^NUMBER_LIMIT_IMPORTANT="10"/NUMBER_LIMIT_IMPORTANT="5"/' /etc/snapper/configs/home

# Space usage limits (fraction of filesystem)
sudo sed -i 's/^SPACE_LIMIT="0.5"/SPACE_LIMIT="0.3"/' /etc/snapper/configs/root
sudo sed -i 's/^SPACE_LIMIT="0.5"/SPACE_LIMIT="0.3"/' /etc/snapper/configs/home

# Minimum free space to maintain
sudo sed -i 's/^FREE_LIMIT="0.2"/FREE_LIMIT="0.3"/' /etc/snapper/configs/root
sudo sed -i 's/^FREE_LIMIT="0.2"/FREE_LIMIT="0.3"/' /etc/snapper/configs/home
```

> [!TIP]
> **What these settings mean:**
> - `TIMELINE_CREATE="no"` — No hourly automatic snapshots. Snapshots are only created manually or by `snap-pac` on pacman operations.
> - `NUMBER_LIMIT="5"` — Keep at most 5 numbered snapshots.
> - `NUMBER_LIMIT_IMPORTANT="5"` — Keep at most 5 important snapshots.
> - `SPACE_LIMIT="0.3"` — Delete old snapshots if they use more than 30% of the filesystem.
> - `FREE_LIMIT="0.3"` — Delete old snapshots if free space drops below 30%.
>
> If you **want** timeline snapshots (hourly/daily/weekly/monthly), set `TIMELINE_CREATE="yes"` and also configure `TIMELINE_LIMIT_HOURLY`, `TIMELINE_LIMIT_DAILY`, etc.

### 3.7 Allow Your User to Use Snapper (Optional)

```bash
# Add your user to the snapper-managed groups
sudo sed -i "s/^ALLOW_USERS=\"\"/ALLOW_USERS=\"$USER\"/" /etc/snapper/configs/root
sudo sed -i "s/^ALLOW_USERS=\"\"/ALLOW_USERS=\"$USER\"/" /etc/snapper/configs/home
```

---

## Part 4 — Limine–Snapper Integration Packages

These packages provide the "sync" functionality that copies kernels into snapshots and manages Limine boot entries automatically.

> [!NOTE]
> The packages `limine-snapper-sync` and `limine-mkinitcpio-hook` originate from the **[omarchy project](https://github.com/basecamp/omarchy)**. They may be available in **AUR** or in the **omarchy custom pacman repo**. Search AUR first. If they are not in AUR, check the omarchy GitHub for PKGBUILDs or the repo URL.

### 4.1 Install with AUR Helper

```bash
# Search AUR for the packages
paru -Ss limine-snapper-sync
paru -Ss limine-mkinitcpio-hook
```

```bash
# Install them
paru -S limine-snapper-sync limine-mkinitcpio-hook
```

> [!TIP]
> If using `yay` instead of `paru`:
> ```bash
> yay -S limine-snapper-sync limine-mkinitcpio-hook
> ```

### 4.2 What These Packages Provide

**`limine-mkinitcpio-hook`:**

| File/Command | Purpose |
|---|---|
| `/usr/bin/limine-update` | Regenerates `/boot/limine.conf` boot entries from `/etc/default/limine` |
| Pacman alpm hooks | Automatically runs `limine-update` when kernel/initramfs changes |
| `btrfs-overlayfs` mkinitcpio hook | Allows booting read-only snapshots via overlayfs |

**`limine-snapper-sync`:**

| File/Command | Purpose |
|---|---|
| `limine-snapper-sync.service` | Systemd service that syncs kernel/initramfs into BTRFS snapshots |
| `/usr/bin/limine-snapper-restore` | CLI tool to restore a system from a snapshot |

### 4.3 If Packages Are Not Available — Manual Pacman Hook

If you cannot find the AUR packages, create a manual pacman hook to keep Limine config in sync when the kernel updates:

```bash
sudo mkdir -p /etc/pacman.d/hooks
```

```bash
sudo tee /etc/pacman.d/hooks/99-limine-update.hook << 'EOF'
[Trigger]
Type = Path
Operation = Install
Operation = Upgrade
Operation = Remove
Target = usr/lib/modules/*/vmlinuz
Target = usr/lib/initcpio/*
Target = boot/vmlinuz-*
Target = boot/initramfs-*

[Action]
Description = Updating Limine boot configuration...
When = PostTransaction
Exec = /bin/bash -c 'echo "Limine: Kernel updated. Verify /boot/limine.conf if needed."'
NeedsTargets
EOF
```

> [!NOTE]
> The manual hook above is a **notification only** — it reminds you to verify your limine.conf. The full `limine-update` command from the AUR package does the actual regeneration automatically. If you go the manual route, you must update `/boot/limine.conf` yourself when switching kernels.

---

## Part 5 — Configure mkinitcpio for Encrypted BTRFS

### 5.1 Understanding the Hook Order

For a LUKS-encrypted BTRFS root with snapshot boot support, the hooks must be in a specific order:

```
HOOKS=(base udev keyboard autodetect microcode modconf kms keymap consolefont block encrypt filesystems fsck btrfs-overlayfs)
```

**Why this order matters:**

| Hook | Purpose | Order Reason |
|---|---|---|
| `base` | Core utilities | Always first |
| `udev` | Device manager | Needed for device detection |
| `keyboard` | Keyboard drivers | **Must be before `autodetect`** so keyboard works for LUKS password |
| `autodetect` | Reduces initramfs to needed modules | After keyboard to not exclude keyboard drivers |
| `microcode` | CPU microcode early loading | After autodetect |
| `modconf` | Module config from `/etc/modprobe.d/` | After autodetect |
| `kms` | Kernel Mode Setting | Early display init |
| `keymap` | Console keymap | After keyboard |
| `consolefont` | Console font | After keymap |
| `block` | Block device modules | Before encrypt |
| `encrypt` | LUKS decryption | **Must be before `filesystems`** |
| `filesystems` | Filesystem modules (btrfs, ext4, etc.) | After encrypt |
| `fsck` | Filesystem check | After filesystems |
| `btrfs-overlayfs` | Snapshot overlay boot support | Last |

> [!WARNING]
> If `keyboard` is placed **after** `autodetect`, the autodetect hook may not include your keyboard driver in the initramfs, and you will be **unable to type your LUKS passphrase**. Always place `keyboard` before `autodetect`.

### 5.2 Create the mkinitcpio Drop-In Config

Drop-in files in `/etc/mkinitcpio.conf.d/` override variables from the main `/etc/mkinitcpio.conf` without modifying the original file.

```bash
sudo tee /etc/mkinitcpio.conf.d/hooks.conf << 'EOF'
HOOKS=(base udev keyboard autodetect microcode modconf kms keymap consolefont block encrypt filesystems fsck btrfs-overlayfs)
EOF
```

> [!NOTE]
> - If you use **Plymouth** (graphical boot splash), add `plymouth` after `udev`:
>   ```
>   HOOKS=(base udev plymouth keyboard autodetect ...)
>   ```
> - If you do **NOT** have the `btrfs-overlayfs` hook installed (from `limine-mkinitcpio-hook`), remove it:
>   ```
>   HOOKS=(base udev keyboard autodetect microcode modconf kms keymap consolefont block encrypt filesystems fsck)
>   ```
> - If you use **sd-encrypt** (systemd-based) instead of `encrypt`, use `systemd` instead of `udev`, and `sd-encrypt` instead of `encrypt`. Your kernel cmdline also changes to `rd.luks.name=UUID=cryptroot`.

### 5.3 Add Extra Modules (Optional)

If you need specific kernel modules available in the initramfs (e.g., Thunderbolt for external drives):

```bash
sudo tee /etc/mkinitcpio.conf.d/extra_modules.conf << 'EOF'
MODULES+=(thunderbolt)
EOF
```

> [!TIP]
> **How drop-in files work:**
> - Files in `/etc/mkinitcpio.conf.d/` are sourced as shell scripts **after** the main config.
> - `VARIABLE=(...)` **overrides** the variable entirely.
> - `VARIABLE+=(...)` **appends** to the existing array.
> - Files are processed in alphabetical order.

### 5.4 Regenerate the Initramfs

```bash
sudo mkinitcpio -P
```

This rebuilds all initramfs images (for all installed kernels).

```bash
# Verify the images were created
ls -la /boot/initramfs-*.img
```

---

## Part 6 — Configure limine-update & Finalize Boot

### 6.1 Create /etc/default/limine

This config file is read by `limine-update` to generate boot entries:

```bash
# Get your current cmdline
LUKS_UUID=$(sudo blkid -s UUID -o value /dev/nvme0n1p2)
echo "Your LUKS UUID: $LUKS_UUID"
```

```bash
sudo tee /etc/default/limine << EOF
# Limine boot configuration defaults
# Used by limine-update to generate /boot/limine.conf entries

CMDLINE="cryptdevice=UUID=$LUKS_UUID:cryptroot root=/dev/mapper/cryptroot rootflags=subvol=@ rw quiet"
ENABLE_UKI=yes
ENABLE_LIMINE_FALLBACK=yes
EOF
```

> [!NOTE]
> - `ENABLE_UKI=yes` — Enable Unified Kernel Image support (EFI only).
> - `ENABLE_LIMINE_FALLBACK=yes` — Generate a fallback initramfs boot entry.
> - If you're on **BIOS** (not EFI), remove both `ENABLE_UKI` and `ENABLE_LIMINE_FALLBACK` lines.

### 6.2 Set Up Base limine.conf

The base `limine.conf` provides global settings. `limine-update` appends boot entries to it:

```bash
sudo tee /boot/limine.conf << 'EOF'
timeout: 5
verbose: no
EOF
```

### 6.3 Run limine-update

```bash
sudo limine-update
```

### 6.4 Verify Boot Entries

```bash
cat /boot/limine.conf
```

You should see your base config **plus** auto-generated entries (prefixed with `/+`):

```
timeout: 5
verbose: no

/+Arch Linux
    protocol: linux
    kernel_path: boot():/vmlinuz-linux
    cmdline: cryptdevice=UUID=xxxx:cryptroot root=/dev/mapper/cryptroot rootflags=subvol=@ rw quiet
    module_path: boot():/initramfs-linux.img
...
```

```bash
# Verify auto-generated entries exist
grep -q "^/+" /boot/limine.conf && echo "✅ Boot entries found" || echo "❌ ERROR: No boot entries generated"
```

### 6.5 Clean Up Old EFI Boot Entries (Optional)

If you previously used another bootloader (e.g., GRUB, systemd-boot), you may want to clean up stale EFI entries:

```bash
# List all EFI boot entries
efibootmgr -v
```

```bash
# Remove an entry by boot number (e.g., 0003)
# sudo efibootmgr -b 0003 -B
```

> [!WARNING]
> Only remove old bootloader entries **after** confirming Limine boots correctly. Keep at least one working entry as a fallback until you're confident.

### 6.6 Reboot and Test

```bash
reboot
```

Verify:
- Limine boot menu appears
- You can type your LUKS passphrase
- System boots to your desktop

---

## Part 7 — Enable Services

### 7.1 Snapper Services

```bash
# Enable automatic snapshot cleanup (runs daily)
sudo systemctl enable --now snapper-cleanup.timer
```

If you chose to enable timeline snapshots (`TIMELINE_CREATE="yes"`):

```bash
# Enable timeline snapshot creation (runs hourly)
sudo systemctl enable --now snapper-timeline.timer
```

### 7.2 Limine-Snapper Sync Service

This service watches for new Snapper snapshots and syncs the kernel/initramfs into them so snapshots are bootable:

```bash
sudo systemctl enable --now limine-snapper-sync.service
```

### 7.3 Verify Services Are Running

```bash
systemctl status snapper-cleanup.timer
systemctl status limine-snapper-sync.service
```

---

## Part 8 — Usage: Creating & Managing Snapshots

### Create a Manual Snapshot

```bash
# Snapshot root
sudo snapper -c root create -c number -d "Before system update"

# Snapshot home
sudo snapper -c home create -c number -d "Before system update"
```

### Create Pre/Post Snapshot Pair

```bash
# Create a "pre" snapshot, do your changes, then create a "post" snapshot
sudo snapper -c root create -t pre -d "System update" --print-number
# ... do your update ...
sudo snapper -c root create -t post --pre-number <PRE_NUMBER> -d "System update"
```

> [!TIP]
> If you installed `snap-pac`, pre/post snapshot pairs are created **automatically** every time you run `pacman -S`, `pacman -R`, or `pacman -U`. No manual action needed for package operations.

### List Snapshots

```bash
# List root snapshots
sudo snapper -c root list

# List home snapshots
sudo snapper -c home list
```

### View Changes Between Snapshots

```bash
# Show files that changed between snapshot 1 and 2
sudo snapper -c root status 1..2
```

### Delete a Snapshot

```bash
# Delete snapshot number 3
sudo snapper -c root delete 3
```

### Delete a Range of Snapshots

```bash
sudo snapper -c root delete 3-7
```

### Check Disk Usage

```bash
# Show space used by snapshots
sudo btrfs filesystem usage /

# Show individual subvolume sizes (requires quotas enabled)
sudo btrfs qgroup show /
```

---

## Part 9 — Restoring / Rolling Back from Snapshots

### Method 1: Using limine-snapper-restore (Recommended)

If you installed `limine-snapper-sync`:

```bash
sudo limine-snapper-restore
```

This interactive tool lets you choose a snapshot and handles the rollback process.

### Method 2: Boot into a Snapshot from Limine Menu

If `limine-snapper-sync` is working, snapshot boot entries appear in the Limine boot menu automatically. Select one and boot into it. The `btrfs-overlayfs` hook makes the snapshot usable by layering an overlayfs on top of the read-only snapshot.

### Method 3: Manual Rollback from Live USB

If your system is completely unbootable:

```bash
# 1. Boot from Arch Linux live USB

# 2. Open the LUKS volume
cryptsetup open /dev/nvme0n1p2 cryptroot

# 3. Mount the top-level BTRFS volume
mount -o subvolid=5 /dev/mapper/cryptroot /mnt

# 4. List subvolumes and snapshots
btrfs subvolume list /mnt

# 5. Identify the snapshot you want to restore (e.g., @snapshots/1/snapshot)

# 6. Move the broken root subvolume out of the way
mv /mnt/@ /mnt/@.broken

# 7. Snapshot the desired snapshot as the new root
btrfs subvolume snapshot /mnt/@snapshots/1/snapshot /mnt/@

# 8. Unmount
umount /mnt

# 9. Reboot
reboot
```

```bash
# After confirming the restored system works, delete the broken subvolume:
sudo mount -o subvolid=5 /dev/mapper/cryptroot /mnt/btrfs-root
sudo btrfs subvolume delete /mnt/btrfs-root/@.broken
sudo umount /mnt/btrfs-root
```

> [!IMPORTANT]
> **Why the dedicated `@snapshots` subvolume matters:** Because `@snapshots` is a separate top-level subvolume (not nested inside `@`), when you replace `@` with a snapshot, your snapshot metadata in `@snapshots` is preserved. You can still access all your other snapshots after a rollback.

---

## Appendix A — Writing Custom mkinitcpio Hooks

### A.1 Hook Architecture

A mkinitcpio hook has two components:

| File | Location | Runs When |
|---|---|---|
| **Install script** | `/usr/lib/initcpio/install/<hookname>` | At initramfs **build time** (`mkinitcpio -P`) |
| **Runtime script** | `/usr/lib/initcpio/hooks/<hookname>` | At **boot time** inside the initramfs |

### A.2 Install Script Template

The install script determines what binaries, files, and modules get included in the initramfs.

Create `/usr/lib/initcpio/install/my-custom-hook`:

```bash
#!/bin/bash

build() {
    # Add the runtime hook script (this file handles boot-time logic)
    add_runscript

    # Add specific binaries to the initramfs
    add_binary /usr/bin/btrfs
    add_binary /usr/bin/mount.btrfs

    # Add specific files
    add_file /etc/my-custom-config.conf

    # Add kernel modules
    add_module btrfs
    add_module overlay

    # Add all modules matching a filter
    # add_all_modules '/drivers/usb'

    # Add a full directory
    # add_full_dir /etc/my-app
}

help() {
    cat << HELPEOF
This hook does XYZ during early boot.
HELPEOF
}
```

### A.3 Runtime Script Template

The runtime hook runs during boot, inside the initramfs environment. It uses **busybox ash** (not bash).

Create `/usr/lib/initcpio/hooks/my-custom-hook`:

```bash
#!/usr/bin/ash

run_earlyhook() {
    # Runs very early, before any devices are available
    msg "Early hook: setting up..."
}

run_hook() {
    # Main hook logic — runs after devices are available
    # but before root is mounted
    msg "Main hook: running custom logic..."
}

run_latehook() {
    # Runs after root filesystem is mounted (at /new_root)
    msg "Late hook: post-mount tasks..."
}

run_cleanuphook() {
    # Runs at the very end, just before switching to real root
    msg "Cleanup hook: final tasks..."
}
```

> [!NOTE]
> - You don't need to implement all four functions. Only implement the ones you need.
> - `run_hook` is the most commonly used.
> - The runtime script runs in **ash** (not bash) — no bashisms allowed.
> - Use `msg "text"` to print messages during boot.
> - The real root is mounted at `/new_root` during `run_latehook`.

### A.4 Example: Custom Hook for Encrypted BTRFS Overlay

Here's a conceptual example of what a `btrfs-overlayfs` hook looks like (simplified):

**Install script** — `/usr/lib/initcpio/install/btrfs-overlayfs`:

```bash
#!/bin/bash

build() {
    add_runscript
    add_module overlay
    add_module btrfs
    add_binary /usr/bin/btrfs
}

help() {
    cat << HELPEOF
Enables booting from read-only BTRFS snapshots using an overlayfs layer.
Must be placed after 'filesystems' in the HOOKS array.
HELPEOF
}
```

**Runtime script** — `/usr/lib/initcpio/hooks/btrfs-overlayfs`:

```bash
#!/usr/bin/ash

run_latehook() {
    # Check if we're booting a snapshot (indicated by kernel cmdline)
    if grep -q "btrfs_snapshot=" /proc/cmdline; then
        snapshot=$(grep -o 'btrfs_snapshot=[^ ]*' /proc/cmdline | cut -d= -f2)

        msg ":: Booting from BTRFS snapshot: $snapshot"

        # Create overlay directories
        mkdir -p /overlay/upper /overlay/work

        # Mount tmpfs for the writable overlay layer
        mount -t tmpfs tmpfs /overlay

        mkdir -p /overlay/upper /overlay/work

        # Re-mount root as overlay
        mount -t overlay overlay \
            -o lowerdir=/new_root,upperdir=/overlay/upper,workdir=/overlay/work \
            /new_root
    fi
}
```

### A.5 Making the Hook Available

```bash
# Set correct permissions
sudo chmod 644 /usr/lib/initcpio/install/my-custom-hook
sudo chmod 644 /usr/lib/initcpio/hooks/my-custom-hook
```

```bash
# Add to your HOOKS array
sudo tee /etc/mkinitcpio.conf.d/my_hook.conf << 'EOF'
HOOKS=(base udev keyboard autodetect microcode modconf kms keymap consolefont block encrypt filesystems fsck my-custom-hook)
EOF
```

```bash
# Rebuild initramfs
sudo mkinitcpio -P
```

### A.6 Drop-In Config Files Reference

| Directory | Purpose |
|---|---|
| `/etc/mkinitcpio.conf` | Main config (don't edit if using drop-ins) |
| `/etc/mkinitcpio.conf.d/*.conf` | Drop-in overrides (sourced alphabetically after main) |
| `/usr/lib/initcpio/install/` | Hook install scripts (build-time) |
| `/usr/lib/initcpio/hooks/` | Hook runtime scripts (boot-time) |
| `/etc/initcpio/install/` | **User** hook install scripts (overrides `/usr/lib` versions) |
| `/etc/initcpio/hooks/` | **User** hook runtime scripts (overrides `/usr/lib` versions) |

> [!TIP]
> Place custom hooks in `/etc/initcpio/` rather than `/usr/lib/initcpio/` if you want them to survive package updates. Files in `/etc/initcpio/` take priority over `/usr/lib/initcpio/`.

---

## Appendix B — Troubleshooting

### System Won't Boot After Switching to Limine

1. Boot from live USB
2. Mount your ESP: `mount /dev/nvme0n1p1 /mnt`
3. Check `/mnt/limine.conf` for syntax errors
4. Verify `LUKS-UUID` is correct: `blkid /dev/nvme0n1p2`
5. Verify the kernel and initramfs files exist: `ls /mnt/vmlinuz-linux /mnt/initramfs-linux.img`

### "Cannot type LUKS passphrase" (No Keyboard in Initramfs)

Your `keyboard` hook is either missing or placed after `autodetect`:

```bash
# Boot from live USB, chroot into your system
cryptsetup open /dev/nvme0n1p2 cryptroot
mount -o subvol=@ /dev/mapper/cryptroot /mnt
mount /dev/nvme0n1p1 /mnt/boot
arch-chroot /mnt

# Fix hooks — keyboard BEFORE autodetect
nano /etc/mkinitcpio.conf.d/hooks.conf
mkinitcpio -P
exit
umount -R /mnt
reboot
```

### Snapper Error: "No snapper config found"

```bash
# Verify configs exist
ls /etc/snapper/configs/
sudo snapper list-configs

# If configs are missing, recreate
sudo snapper -c root create-config /
sudo snapper -c home create-config /home

# Then redo the subvolume remapping (Part 3, Step 3.3)
```

### Snapshots Using Too Much Disk Space

```bash
# Check space usage
sudo btrfs filesystem usage /
sudo btrfs qgroup show / --raw

# Manually clean up
sudo snapper -c root cleanup number
sudo snapper -c root cleanup timeline

# Or delete specific snapshots
sudo snapper -c root list
sudo snapper -c root delete <number>
```

### limine-update Produces No Entries

```bash
# Check /etc/default/limine exists and is correct
cat /etc/default/limine

# Check that kernel files exist
ls -la /boot/vmlinuz-* /boot/initramfs-*

# Re-run manually
sudo limine-update

# Check for errors in output
```

### Verify Snapshot Subvolumes Are Correctly Separated

```bash
# Mount top-level to check
sudo mount -o subvolid=5 /dev/mapper/cryptroot /mnt/btrfs-root
sudo btrfs subvolume list /mnt/btrfs-root

# @snapshots should be at the TOP LEVEL (parent ID 5), NOT nested under @
# Correct:
#   ID 258 gen ... top level 5 path @snapshots
# Wrong:
#   ID 258 gen ... top level 256 path @/.snapshots

sudo umount /mnt/btrfs-root
```

---

## Quick Reference Card

```bash
# ─── Snapshot Operations ────────────────────────────────
sudo snapper -c root create -c number -d "description"  # Create snapshot
sudo snapper -c root list                                 # List snapshots
sudo snapper -c root delete <N>                          # Delete snapshot N
sudo snapper -c root status <N1>..<N2>                   # Diff between snapshots
sudo snapper -c root undochange <N1>..<N2>               # Undo changes

# ─── Limine Operations ─────────────────────────────────
sudo limine-update                                        # Regenerate boot config
cat /boot/limine.conf                                     # View boot config
sudo limine-snapper-restore                               # Interactive rollback

# ─── BTRFS Operations ──────────────────────────────────
sudo btrfs subvolume list /                               # List subvolumes
sudo btrfs filesystem usage /                             # Disk usage
sudo btrfs qgroup show /                                  # Quota group info
sudo btrfs scrub start /                                  # Start integrity check

# ─── Service Management ────────────────────────────────
systemctl status snapper-cleanup.timer
systemctl status limine-snapper-sync.service
sudo mkinitcpio -P                                        # Rebuild all initramfs
```



---


================================================

# SAME GUIDE BUT SLIGHTLY DIFFERENT, READ THIS AS WELL! 

================================================



# BTRFS Snapshots with Snapper & Limine on Arch Linux

> Complete step-by-step manual for encrypted BTRFS with Snapper snapshots, Limine bootloader, kernel-sync hooks, and boot-from-snapshot integration.
> Arch Linux — latest rolling release (2025).

---

## Table of Contents

- [[#Prerequisites]]
- [[#Part 1 — Understand Your Disk Layout]]
- [[#Part 2 — Install and Deploy Limine Bootloader]]
- [[#Part 3 — Configure Limine]]
- [[#Part 4 — mkinitcpio for Encrypted BTRFS]]
- [[#Part 5 — Writing Custom mkinitcpio Hooks]]
- [[#Part 6 — Create Dedicated Snapshot Subvolumes]]
- [[#Part 7 — Install and Configure Snapper]]
- [[#Part 8 — Limine–Snapper Integration (Kernel Sync & Boot-from-Snapshot)]]
- [[#Part 9 — Using Snapshots Day-to-Day]]
- [[#Part 10 — Emergency Manual Rollback]]
- [[#Part 11 — Maintenance]]
- [[#Appendix A — mkinitcpio Hook Anatomy (Deep Dive)]]
- [[#Appendix B — Useful Commands Quick Reference]]
- [[#Appendix C — Troubleshooting]]

---

## Prerequisites

| Requirement | Details |
|---|---|
| Arch Linux | Installed and booting on BTRFS |
| Encryption | LUKS on the root partition (`/dev/mapper/cryptroot` or similar) |
| ESP | A FAT32 EFI System Partition mounted at `/boot` |
| AUR helper | `paru` or `yay` installed (needed for integration packages) |
| Root access | All commands assume `sudo` or root shell |

**Assumed partition layout:**

```
/dev/sda1  (or nvme0n1p1)  →  ESP (FAT32)      →  mounted at /boot
/dev/sda2  (or nvme0n1p2)  →  LUKS container    →  decrypted as /dev/mapper/cryptroot
                                └── BTRFS filesystem with subvolumes
```

> [!warning]
> Replace `/dev/sda1`, `/dev/sda2`, and `/dev/mapper/cryptroot` with your actual device names throughout this guide. Run `lsblk -f` to confirm.

---

## Part 1 — Understand Your Disk Layout

Before touching anything, survey what you have.

### 1.1 Check block devices and UUIDs

```bash
lsblk -f
```

Note down:
- **LUKS partition UUID** (the UUID of `/dev/sda2`, NOT the decrypted mapper) — you need this for the `encrypt` hook.
- **BTRFS UUID** (the UUID of the filesystem inside the decrypted LUKS) — you need this for `fstab`.

```bash
# Get LUKS partition UUID
sudo blkid /dev/sda2 | grep -oP 'UUID="\K[^"]+'

# Get BTRFS filesystem UUID (inside LUKS)
sudo blkid /dev/mapper/cryptroot | grep -oP 'UUID="\K[^"]+'
```

### 1.2 Check existing BTRFS subvolumes

```bash
sudo btrfs subvolume list /
```

### 1.3 Check current mounts

```bash
findmnt -t btrfs
findmnt /boot
```

### 1.4 Ideal subvolume layout (target)

```
top-level (subvolid=5)
├── @               → mounted at /
├── @home            → mounted at /home
├── @snapshots       → mounted at /.snapshots        ← NEW (for root snapshots)
├── @home_snapshots  → mounted at /home/.snapshots   ← NEW (for home snapshots)
├── @log             → mounted at /var/log
├── @cache           → mounted at /var/cache
└── @tmp             → mounted at /tmp
```

> [!info] Why dedicated snapshot subvolumes?
> If snapshots live *inside* `@` as nested subvolumes, you lose them if you ever need to replace `@` with a snapshot. A separate top-level subvolume (`@snapshots`) is independent — it survives a root rollback.

---

## Part 2 — Install and Deploy Limine Bootloader

### 2.1 Install the package

```bash
sudo pacman -S limine efibootmgr
```

### 2.2 Check if you're booted in UEFI mode

```bash
[[ -d /sys/firmware/efi ]] && echo "UEFI" || echo "BIOS"
```

### 2.3 Deploy Limine — UEFI

```bash
# Create directory on the ESP
sudo mkdir -p /boot/EFI/BOOT

# Copy the Limine EFI binary
sudo cp /usr/share/limine/BOOTX64.EFI /boot/EFI/BOOT/BOOTX64.EFI
```

Register with the UEFI firmware:

```bash
# Find your ESP disk and partition number
# e.g., /dev/sda partition 1, or /dev/nvme0n1 partition 1

sudo efibootmgr --create \
  --disk /dev/sda \
  --part 1 \
  --loader '\EFI\BOOT\BOOTX64.EFI' \
  --label 'Limine' \
  --unicode
```

> [!tip]
> If your ESP is on an NVMe drive, use `--disk /dev/nvme0n1 --part 1`.

Verify the entry was created:

```bash
efibootmgr -v
```

### 2.4 Deploy Limine — BIOS/Legacy (skip if UEFI)

```bash
sudo limine bios-install /dev/sda
```

You also need to copy `limine-bios.sys` to `/boot`:

```bash
sudo cp /usr/share/limine/limine-bios.sys /boot/
```

---

## Part 3 — Configure Limine

### 3.1 Gather your kernel command line

You need the LUKS partition UUID:

```bash
LUKS_UUID=$(sudo blkid -s UUID -o value /dev/sda2)
echo "cryptdevice=UUID=${LUKS_UUID}:cryptroot root=/dev/mapper/cryptroot rootflags=subvol=@ rw"
```

Copy the output — this is your `cmdline`.

### 3.2 Create `/boot/limine.conf`

```bash
sudo nano /boot/limine.conf
```

Paste the following (adjust the `cmdline` to match your output):

```ini
timeout: 5
quiet: no
serial: no

/Arch Linux
    protocol: linux
    cmdline: cryptdevice=UUID=xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx:cryptroot root=/dev/mapper/cryptroot rootflags=subvol=@ rw quiet
    kernel_path: boot():/vmlinuz-linux
    module_path: boot():/initramfs-linux.img

/Arch Linux (Fallback)
    protocol: linux
    cmdline: cryptdevice=UUID=xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx:cryptroot root=/dev/mapper/cryptroot rootflags=subvol=@ rw
    kernel_path: boot():/vmlinuz-linux
    module_path: boot():/initramfs-linux-fallback.img
```

> [!info] What is `boot()`?
> `boot()` refers to the volume (partition) where `limine.conf` was found — your ESP mounted at `/boot`. So `boot():/vmlinuz-linux` resolves to `/boot/vmlinuz-linux`.

> [!info] Microcode
> If you have the `microcode` hook in mkinitcpio (covered below), CPU microcode is embedded in the initramfs — no separate `module_path` needed. If you do NOT use that hook and have a separate microcode image, add it **before** the initramfs:
> ```ini
>     module_path: boot():/intel-ucode.img
>     module_path: boot():/initramfs-linux.img
> ```
> (or `amd-ucode.img` for AMD)

### 3.3 Verify the config

```bash
cat /boot/limine.conf
```

### 3.4 Test the boot

Reboot and verify Limine presents the menu and boots successfully:

```bash
sudo reboot
```

> [!warning]
> If you are switching FROM another bootloader (GRUB, systemd-boot), make sure Limine is the first boot entry. Use `efibootmgr --bootorder` to adjust if needed. Do NOT remove the old bootloader until Limine is confirmed working.

```bash
# Example: set Limine (entry 0005) as first
sudo efibootmgr --bootorder 0005,0001,0002
```

---

## Part 4 — mkinitcpio for Encrypted BTRFS

### 4.1 Understanding the hooks you need

For LUKS-encrypted BTRFS, the critical hooks are:

| Hook | Purpose |
|---|---|
| `base` | Essential base utilities |
| `udev` | Device manager (needed for device detection) |
| `keyboard` | Keyboard support (for LUKS passphrase entry) |
| `autodetect` | Reduces initramfs size by detecting needed modules |
| `microcode` | Embeds CPU microcode into initramfs |
| `modconf` | Loads modules from `/etc/modprobe.d/` |
| `kms` | Kernel Mode Setting (early display) |
| `keymap` | Non-US keyboard layout support |
| `consolefont` | Console font |
| `block` | Block device support |
| `encrypt` | LUKS decryption (**critical**) |
| `filesystems` | Filesystem support (including BTRFS) |
| `fsck` | Filesystem check |
| `btrfs-overlayfs` | Boot from read-only snapshots via overlayfs (**from limine-mkinitcpio-hook**) |

> [!warning] Hook order matters!
> `keyboard` and `keymap` **must** come before `encrypt` so you can type your passphrase.
> `block` must come before `encrypt` so the LUKS partition is detected.
> `encrypt` must come before `filesystems`.

### 4.2 Configure hooks using drop-in files

Modern mkinitcpio supports drop-in config files in `/etc/mkinitcpio.conf.d/`. This is cleaner than editing the main config.

```bash
sudo nano /etc/mkinitcpio.conf.d/hooks.conf
```

Paste:

```bash
HOOKS=(base udev keyboard autodetect microcode modconf kms keymap consolefont block encrypt filesystems fsck)
```

> [!note]
> We'll add `btrfs-overlayfs` later after installing the integration packages (Part 8). For now, this gets you a working encrypted BTRFS boot.

> [!tip] Optional: Plymouth
> If you use Plymouth for a splash screen, the hook order changes:
> ```bash
> HOOKS=(base udev plymouth keyboard autodetect microcode modconf kms keymap consolefont block plymouth-encrypt filesystems fsck)
> ```
> Note: `plymouth-encrypt` replaces `encrypt`. Install `plymouth` first: `sudo pacman -S plymouth`

### 4.3 Optional: add extra modules

If you need Thunderbolt support or other modules:

```bash
sudo nano /etc/mkinitcpio.conf.d/modules.conf
```

```bash
MODULES+=(thunderbolt)
```

### 4.4 Rebuild the initramfs

```bash
sudo mkinitcpio -P
```

This rebuilds initramfs for ALL installed kernels (`-P` = all presets).

Verify the images were created:

```bash
ls -lh /boot/initramfs-*.img
```

---

## Part 5 — Writing Custom mkinitcpio Hooks

> [!info]
> This section explains how mkinitcpio hooks work and how to write your own. If you just want to use pre-built hooks (like `encrypt` and `btrfs-overlayfs`), you can skip this section. It's here for your understanding.

### 5.1 Hook file locations

A mkinitcpio hook has **two** components:

| Component | Location | Purpose |
|---|---|---|
| **Build hook (install script)** | `/usr/lib/initcpio/install/<hookname>` | Defines what files, binaries, and modules to include in the initramfs image |
| **Runtime hook** | `/usr/lib/initcpio/hooks/<hookname>` | Defines what code runs during boot (early userspace) |

> [!important]
> Custom hooks go in the same directories. Packages install their hooks there automatically. If you write your own, place them in these paths.

### 5.2 Build hook (install script) anatomy

Create `/usr/lib/initcpio/install/my-custom-hook`:

```bash
#!/bin/bash

build() {
    # Add kernel modules to the initramfs
    add_module "btrfs"

    # Add binaries to the initramfs
    add_binary "btrfs"
    add_binary "/usr/bin/my-tool"

    # Add files
    add_file "/etc/my-config"

    # Add the runtime hook
    add_runscript
}

help() {
    cat <<HELPEOF
    This hook does XYZ during early boot.
HELPEOF
}
```

Key functions available in build hooks:

| Function | Purpose |
|---|---|
| `add_module "name"` | Include a kernel module |
| `add_binary "path"` | Include a binary and its library dependencies |
| `add_file "path"` | Include a file as-is |
| `add_dir "path"` | Include a directory |
| `add_runscript` | Include the corresponding runtime hook from `/usr/lib/initcpio/hooks/` |
| `add_full_dir "path"` | Recursively include a directory |

### 5.3 Runtime hook anatomy

Create `/usr/lib/initcpio/hooks/my-custom-hook`:

```bash
#!/usr/bin/ash

run_hook() {
    # This function runs during boot
    # The root filesystem is NOT yet mounted
    # /new_root is where the root will be mounted

    msg "My custom hook is running..."

    # Example: do something before root mount
    # The 'encrypt' hook has already decrypted LUKS by this point
    # (if your hook runs after 'encrypt' in the HOOKS array)
}

run_cleanuphook() {
    # Optional: runs after root is mounted
    # Useful for cleanup tasks
}
```

> [!warning]
> Runtime hooks use `/bin/ash` (BusyBox shell), NOT bash. Keep syntax POSIX-compatible. No bashisms.

### 5.4 Example: a minimal encrypted BTRFS verification hook

`/usr/lib/initcpio/install/btrfs-check`:

```bash
#!/bin/bash

build() {
    add_module "btrfs"
    add_binary "btrfs"
    add_runscript
}

help() {
    cat <<HELPEOF
    Runs a quick btrfs device scan after LUKS decryption.
HELPEOF
}
```

`/usr/lib/initcpio/hooks/btrfs-check`:

```bash
#!/usr/bin/ash

run_hook() {
    # Scan for btrfs devices after LUKS decryption
    if [ -x /usr/bin/btrfs ]; then
        /usr/bin/btrfs device scan
    fi
}
```

Then add `btrfs-check` to your HOOKS array (after `encrypt`, before `filesystems`).

### 5.5 Where the `encrypt` hook lives (for reference)

The standard `encrypt` hook comes from the `cryptsetup` package:

```bash
# Build hook
cat /usr/lib/initcpio/install/encrypt

# Runtime hook
cat /usr/lib/initcpio/hooks/encrypt
```

Study these to understand how LUKS decryption works during boot. The `encrypt` hook reads `cryptdevice=` from the kernel command line, opens the LUKS container with `cryptsetup open`, and makes the decrypted device available at `/dev/mapper/<name>`.

### 5.6 Testing your hooks

After creating or modifying hooks:

```bash
# Rebuild initramfs
sudo mkinitcpio -P

# Check for errors in the output
# You can also examine the initramfs contents:
lsinitcpio /boot/initramfs-linux.img | grep "my-custom"
```

---

## Part 6 — Create Dedicated Snapshot Subvolumes

> [!important] Do this BEFORE configuring Snapper
> We create the subvolumes first, then configure Snapper to use them.

### 6.1 Mount the top-level BTRFS filesystem

```bash
sudo mount -o subvolid=5 /dev/mapper/cryptroot /mnt
```

### 6.2 Check what subvolumes already exist

```bash
ls /mnt/
sudo btrfs subvolume list /mnt
```

You should see `@`, `@home`, and possibly `@log`, `@cache`, `@tmp`.

### 6.3 Create the snapshot subvolumes

```bash
# For root snapshots
sudo btrfs subvolume create /mnt/@snapshots

# For home snapshots
sudo btrfs subvolume create /mnt/@home_snapshots
```

### 6.4 Unmount the top-level

```bash
sudo umount /mnt
```

### 6.5 Create mountpoints

```bash
sudo mkdir -p /.snapshots
sudo mkdir -p /home/.snapshots
```

### 6.6 Mount the new subvolumes

```bash
sudo mount -o subvol=@snapshots,compress=zstd,noatime /dev/mapper/cryptroot /.snapshots
sudo mount -o subvol=@home_snapshots,compress=zstd,noatime /dev/mapper/cryptroot /home/.snapshots
```

### 6.7 Set permissions

```bash
sudo chmod 750 /.snapshots
sudo chmod 750 /home/.snapshots
```

### 6.8 Add to `/etc/fstab`

```bash
sudo nano /etc/fstab
```

Add these lines (use the **BTRFS filesystem UUID**, not the LUKS UUID):

```fstab
# Root snapshots
UUID=<BTRFS-UUID>  /.snapshots       btrfs  subvol=@snapshots,compress=zstd,noatime       0 0

# Home snapshots
UUID=<BTRFS-UUID>  /home/.snapshots  btrfs  subvol=@home_snapshots,compress=zstd,noatime   0 0
```

> [!tip] Get your BTRFS UUID
> ```bash
> sudo blkid /dev/mapper/cryptroot -s UUID -o value
> ```

### 6.9 Verify the fstab

```bash
# Unmount and remount everything from fstab to test
sudo umount /.snapshots
sudo umount /home/.snapshots
sudo mount -a

# Verify mounts
findmnt /.snapshots
findmnt /home/.snapshots
```

---

## Part 7 — Install and Configure Snapper

### 7.1 Install Snapper

```bash
sudo pacman -S snapper
```

### 7.2 Create Snapper configurations

For root (`/`):

```bash
sudo snapper -c root create-config /
```

> [!warning] Snapper creates a nested `.snapshots` subvolume
> When you run `create-config`, Snapper creates its own `.snapshots` subvolume nested inside `@`. Since we already have a dedicated `@snapshots` subvolume mounted at `/.snapshots`, we need to handle the conflict.

**Check what happened:**

```bash
sudo btrfs subvolume list / | grep snapshots
```

If Snapper created a nested subvolume, you'll see TWO snapshot-related entries. Fix it:

```bash
# Unmount our dedicated subvolume temporarily
sudo umount /.snapshots

# Delete the auto-created nested subvolume
sudo btrfs subvolume delete /.snapshots

# Recreate the mountpoint
sudo mkdir -p /.snapshots

# Remount our dedicated subvolume
sudo mount -a

# Fix permissions
sudo chmod 750 /.snapshots
```

For home (`/home`):

```bash
sudo snapper -c home create-config /home
```

Same fix:

```bash
sudo umount /home/.snapshots
sudo btrfs subvolume delete /home/.snapshots
sudo mkdir -p /home/.snapshots
sudo mount -a
sudo chmod 750 /home/.snapshots
```

### 7.3 Verify Snapper configs

```bash
sudo snapper list-configs
```

Expected output:

```
Config | Subvolume
-------+----------
root   | /
home   | /home
```

### 7.4 Enable BTRFS quotas

Quotas enable Snapper's space-aware cleanup algorithms (`SPACE_LIMIT` and `FREE_LIMIT`):

```bash
sudo btrfs quota enable /
```

> [!note] Performance impact
> BTRFS quotas have a minor performance overhead. On modern SSDs this is negligible. If you experience issues, you can disable them later with `btrfs quota disable /`.

### 7.5 Tweak Snapper configuration

Edit both config files:

```bash
sudo nano /etc/snapper/configs/root
sudo nano /etc/snapper/configs/home
```

Or apply the changes in bulk:

```bash
# Disable automatic timeline snapshots (we'll create snapshots manually or via snap-pac)
sudo sed -i 's/^TIMELINE_CREATE="yes"/TIMELINE_CREATE="no"/' /etc/snapper/configs/{root,home}

# Limit number of regular snapshots to keep
sudo sed -i 's/^NUMBER_LIMIT="50"/NUMBER_LIMIT="5"/' /etc/snapper/configs/{root,home}

# Limit number of important snapshots to keep
sudo sed -i 's/^NUMBER_LIMIT_IMPORTANT="10"/NUMBER_LIMIT_IMPORTANT="5"/' /etc/snapper/configs/{root,home}

# Maximum disk space for snapshots (fraction of filesystem)
sudo sed -i 's/^SPACE_LIMIT="0.5"/SPACE_LIMIT="0.3"/' /etc/snapper/configs/{root,home}

# Minimum free space to maintain (fraction of filesystem)
sudo sed -i 's/^FREE_LIMIT="0.2"/FREE_LIMIT="0.3"/' /etc/snapper/configs/{root,home}
```

> [!info] What these settings mean
> | Setting | Value | Meaning |
> |---|---|---|
> | `TIMELINE_CREATE` | `no` | Don't auto-create hourly snapshots |
> | `NUMBER_LIMIT` | `5` | Keep max 5 numbered (manual) snapshots |
> | `NUMBER_LIMIT_IMPORTANT` | `5` | Keep max 5 important snapshots |
> | `SPACE_LIMIT` | `0.3` | Snapshots can use up to 30% of the filesystem |
> | `FREE_LIMIT` | `0.3` | Always keep 30% of the filesystem free |

### 7.6 Enable Snapper cleanup timer

```bash
sudo systemctl enable --now snapper-cleanup.timer
```

This periodically removes old snapshots based on the limits you set.

### 7.7 Install snap-pac (optional but recommended)

`snap-pac` automatically creates before/after snapshots for every `pacman` transaction:

```bash
sudo pacman -S snap-pac
```

No configuration needed — it works via pacman hooks automatically.

### 7.8 Test snapshot creation

```bash
sudo snapper -c root create -c number -d "Initial test snapshot"
sudo snapper -c root list
```

Verify the snapshot is stored in the dedicated subvolume:

```bash
ls /.snapshots/
```

You should see a numbered directory (e.g., `1/`) containing `info.xml` and a `snapshot` subvolume.

---

## Part 8 — Limine–Snapper Integration (Kernel Sync & Boot-from-Snapshot)

This is where we connect everything together. The integration packages provide:

| Tool | Purpose |
|---|---|
| `limine-mkinitcpio-hook` | Pacman hook that runs `limine-update` when kernels are installed/removed |
| `limine-update` | Regenerates `/boot/limine.conf` boot entries from `/etc/default/limine` |
| `limine-snapper-sync` | Systemd service that adds snapshot boot entries to Limine when snapshots are created |
| `limine-snapper-restore` | Script to restore the system when booted from a snapshot |
| `btrfs-overlayfs` hook | mkinitcpio hook that enables booting from read-only snapshots via overlayfs |

### 8.1 Install the integration packages

These packages may be in the AUR. Install with your AUR helper:

```bash
paru -S limine-snapper-sync limine-mkinitcpio-hook
```

> [!note]
> If these are not found in the AUR, they may be in a community repository (such as the omarchy or CachyOS repo). Check the project sources:
> - Search AUR: `https://aur.archlinux.org/packages?K=limine-snapper`
> - Check if they were renamed or merged into another package

### 8.2 Update mkinitcpio hooks to include `btrfs-overlayfs`

Now that the package is installed, add the hook:

```bash
sudo nano /etc/mkinitcpio.conf.d/hooks.conf
```

```bash
HOOKS=(base udev keyboard autodetect microcode modconf kms keymap consolefont block encrypt filesystems fsck btrfs-overlayfs)
```

Rebuild initramfs:

```bash
sudo mkinitcpio -P
```

### 8.3 Configure `/etc/default/limine`

This file controls how `limine-update` generates boot entries.

```bash
sudo nano /etc/default/limine
```

```bash
# Kernel command line (your encrypted BTRFS cmdline)
CMDLINE="cryptdevice=UUID=xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx:cryptroot root=/dev/mapper/cryptroot rootflags=subvol=@ rw quiet"

# Generate Unified Kernel Images (UEFI only)
ENABLE_UKI=no

# Create a fallback Limine EFI entry (UEFI only)
ENABLE_LIMINE_FALLBACK=yes
```

> [!important]
> Replace the UUID with your actual LUKS partition UUID. Use the same cmdline you put in `limine.conf` earlier.

> [!tip] For BIOS systems
> Remove or comment out `ENABLE_UKI` and `ENABLE_LIMINE_FALLBACK` — they're UEFI-only features.

### 8.4 Set up the base `/boot/limine.conf`

`limine-update` will append auto-generated entries to `limine.conf`. Set up a clean base:

```bash
sudo nano /boot/limine.conf
```

```ini
timeout: 5
quiet: no
serial: no
```

> [!info]
> That's it — just the global settings. `limine-update` will add the `/Arch Linux`, fallback, and snapshot entries automatically below this.

### 8.5 Run `limine-update`

```bash
sudo limine-update
```

Verify entries were added:

```bash
cat /boot/limine.conf
```

You should see auto-generated entries starting with `/` (like `/Arch Linux`, `/Arch Linux (Fallback)`, etc.).

Check specifically for boot entries:

```bash
grep "^/" /boot/limine.conf
```

> [!warning] If no entries appear
> Something went wrong. Check:
> - Is `/etc/default/limine` formatted correctly?
> - Are kernels installed at `/boot/vmlinuz-*`?
> - Run `limine-update` with verbose output if available

### 8.6 Ensure mkinitcpio pacman hooks are enabled

The integration relies on pacman hooks to auto-regenerate when kernels are updated:

```bash
# These should exist and NOT be .disabled
ls /usr/share/libalpm/hooks/90-mkinitcpio-install.hook
ls /usr/share/libalpm/hooks/60-mkinitcpio-remove.hook
```

If they're disabled (have `.disabled` extension), re-enable them:

```bash
sudo mv /usr/share/libalpm/hooks/90-mkinitcpio-install.hook.disabled \
        /usr/share/libalpm/hooks/90-mkinitcpio-install.hook 2>/dev/null

sudo mv /usr/share/libalpm/hooks/60-mkinitcpio-remove.hook.disabled \
        /usr/share/libalpm/hooks/60-mkinitcpio-remove.hook 2>/dev/null
```

### 8.7 Enable the snapshot-sync service

```bash
sudo systemctl enable --now limine-snapper-sync.service
```

This service watches for new Snapper snapshots and automatically adds Limine boot entries for them, so you can boot directly into any snapshot from the Limine menu.

### 8.8 Clean up old EFI boot entries (if switching from another bootloader)

If you previously installed via `archinstall` or another tool, there may be stale EFI entries:

```bash
# List all EFI boot entries
efibootmgr

# Remove a specific entry (e.g., old GRUB or duplicate Limine entry)
# Replace XXXX with the boot number
sudo efibootmgr -b XXXX -B
```

### 8.9 Verify everything

```bash
# 1. Snapper configs exist
sudo snapper list-configs

# 2. Snapshot subvolumes are mounted
findmnt /.snapshots
findmnt /home/.snapshots

# 3. Limine config has boot entries
grep "^/" /boot/limine.conf

# 4. Snapper sync service is running
systemctl status limine-snapper-sync.service

# 5. Create a test snapshot and check if Limine picks it up
sudo snapper -c root create -c number -d "Test snapshot"
sleep 2
cat /boot/limine.conf | tail -20

# 6. List snapshots
sudo snapper -c root list
```

Reboot and verify the Limine menu shows your entries (and any snapshot entries):

```bash
sudo reboot
```

---

## Part 9 — Using Snapshots Day-to-Day

### 9.1 Create a manual snapshot

```bash
# Root filesystem
sudo snapper -c root create -c number -d "Before major update"

# Home directory
sudo snapper -c home create -c number -d "Before major update"

# Create for ALL configs at once
for config in $(sudo snapper --csvout list-configs | awk -F, 'NR>1 {print $1}'); do
    sudo snapper -c "$config" create -c number -d "Pre-update $(date +%Y-%m-%d)"
done
```

### 9.2 List snapshots

```bash
sudo snapper -c root list
sudo snapper -c home list
```

### 9.3 Compare snapshots (see what changed)

```bash
# Compare snapshot 1 with current state (snapshot 0)
sudo snapper -c root status 1..0

# See file-level diff
sudo snapper -c root diff 1..0
```

### 9.4 Restore individual files from a snapshot

```bash
# Restore a specific file from snapshot #3
sudo cp /.snapshots/3/snapshot/etc/pacman.conf /etc/pacman.conf
```

### 9.5 Delete a snapshot

```bash
sudo snapper -c root delete 3
```

### 9.6 Undo changes between two snapshots

```bash
# Undo all changes between snapshot 1 and snapshot 2
sudo snapper -c root undochange 1..2
```

### 9.7 Boot from a snapshot (via Limine)

1. Reboot the machine
2. In the Limine boot menu, select the snapshot entry
3. The system boots from the snapshot using `btrfs-overlayfs` (read-only snapshot + writable overlay)
4. Test if everything works
5. If you want to make it permanent, run:

```bash
sudo limine-snapper-restore
```

---

## Part 10 — Emergency Manual Rollback

If `limine-snapper-restore` isn't available or the system won't boot at all, use this procedure from a **live USB**.

### 10.1 Boot from Arch Linux live USB

### 10.2 Decrypt and mount

```bash
# Open LUKS
cryptsetup open /dev/sda2 cryptroot

# Mount the top-level BTRFS (subvolid=5)
mount -o subvolid=5 /dev/mapper/cryptroot /mnt
```

### 10.3 Survey the situation

```bash
# List all subvolumes
btrfs subvolume list /mnt

# List available snapshots
ls /mnt/@snapshots/

# Check snapshot info
cat /mnt/@snapshots/1/info.xml
```

### 10.4 Replace root with a snapshot

```bash
# Rename the broken root
mv /mnt/@ /mnt/@.broken

# Create a read-write snapshot from the desired snapshot
# Replace <N> with the snapshot number you want to restore
btrfs subvolume snapshot /mnt/@snapshots/<N>/snapshot /mnt/@
```

### 10.5 (Optional) Restore home too

```bash
mv /mnt/@home /mnt/@home.broken
btrfs subvolume snapshot /mnt/@home_snapshots/<N>/snapshot /mnt/@home
```

### 10.6 Clean up and reboot

```bash
umount /mnt
cryptsetup close cryptroot
reboot
```

### 10.7 After successful reboot, delete the broken subvolumes

```bash
sudo mount -o subvolid=5 /dev/mapper/cryptroot /mnt
sudo btrfs subvolume delete /mnt/@.broken
sudo btrfs subvolume delete /mnt/@home.broken  # if applicable
sudo umount /mnt
```

---

## Part 11 — Maintenance

### 11.1 Monitor disk usage

```bash
# Overall filesystem usage
sudo btrfs filesystem usage /

# Space used by snapshots (requires quotas enabled)
sudo btrfs qgroup show /

# Detailed subvolume space
sudo btrfs subvolume list / | head
sudo btrfs filesystem df /
```

### 11.2 Manual cleanup

```bash
# Trigger Snapper cleanup manually
sudo snapper -c root cleanup number
sudo snapper -c home cleanup number
```

### 11.3 Re-sync Limine after manual changes

If you manually add/remove kernels or snapshots:

```bash
sudo limine-update
```

### 11.4 Defragment (optional, for HDDs)

```bash
sudo btrfs filesystem defragment -r /
```

> [!warning]
> Defragmenting breaks reflinks between snapshots and can significantly increase disk usage. Only do this if you understand the implications.

### 11.5 Scrub (periodic data integrity check)

```bash
sudo btrfs scrub start /
sudo btrfs scrub status /
```

Enable the systemd timer for automatic monthly scrubs:

```bash
sudo systemctl enable --now btrfs-scrub@-.timer
```

---

## Appendix A — mkinitcpio Hook Anatomy (Deep Dive)

### File structure

```
/usr/lib/initcpio/
├── install/           ← Build hooks (what to pack into initramfs)
│   ├── base
│   ├── udev
│   ├── keyboard
│   ├── encrypt        ← LUKS decryption build hook
│   ├── filesystems
│   ├── btrfs-overlayfs ← Snapshot overlay build hook
│   └── my-hook        ← Your custom hook
│
└── hooks/             ← Runtime hooks (what to run during boot)
    ├── encrypt         ← LUKS decryption runtime hook
    ├── btrfs-overlayfs ← Snapshot overlay runtime hook
    └── my-hook         ← Your custom hook
```

### How the `encrypt` hook works (simplified)

**Build hook** (`/usr/lib/initcpio/install/encrypt`):
- Adds `cryptsetup` binary to initramfs
- Adds `dm-crypt` kernel module
- Adds the runtime hook

**Runtime hook** (`/usr/lib/initcpio/hooks/encrypt`):
1. Reads `cryptdevice=UUID=...:name` from kernel command line
2. Resolves the UUID to a block device
3. Runs `cryptsetup open /dev/sdXN name`
4. Prompts for passphrase
5. Makes `/dev/mapper/name` available for the `filesystems` hook to mount

### How the `btrfs-overlayfs` hook works (simplified)

**Purpose:** When booting from a snapshot, sets up an overlayfs so the read-only snapshot appears writable without modifying the actual snapshot data.

**Runtime flow:**
1. Detects if booting from a snapshot (via kernel cmdline parameter or subvol path)
2. Mounts the snapshot read-only
3. Creates a tmpfs for the writable upper layer
4. Creates an overlayfs combining the read-only snapshot (lower) and tmpfs (upper)
5. Pivots root to the overlayfs
6. Result: system appears normal and writable, but no changes persist (unless you run the restore tool)

### Drop-in config files

Instead of editing `/etc/mkinitcpio.conf` directly, use drop-in files:

```
/etc/mkinitcpio.conf.d/
├── hooks.conf           ← HOOKS=(...) override
├── modules.conf         ← MODULES+=(...) additions
└── compression.conf     ← COMPRESSION settings
```

> [!tip]
> Drop-in files **override** the corresponding variable from the main config. If you set `HOOKS=(...)` in a drop-in, it completely replaces the `HOOKS` from `/etc/mkinitcpio.conf`. Use `MODULES+=(...)` (with `+=`) to append instead of replace.

---

## Appendix B — Useful Commands Quick Reference

```bash
# ─── BTRFS ───────────────────────────────────────────────
btrfs subvolume list /                    # List all subvolumes
btrfs subvolume create /mnt/@name         # Create subvolume
btrfs subvolume delete /mnt/@name         # Delete subvolume
btrfs subvolume snapshot /source /dest    # Create snapshot (rw)
btrfs subvolume snapshot -r /source /dest # Create snapshot (ro)
btrfs filesystem usage /                  # Disk usage
btrfs filesystem df /                     # Allocation info
btrfs scrub start /                       # Start integrity check
btrfs quota enable /                      # Enable quotas
btrfs qgroup show /                       # Show quota groups

# ─── SNAPPER ─────────────────────────────────────────────
snapper -c root create-config /           # Create config for /
snapper list-configs                      # List all configs
snapper -c root list                      # List snapshots
snapper -c root create -c number -d "msg" # Create snapshot
snapper -c root delete <N>                # Delete snapshot N
snapper -c root status <N1>..<N2>         # Show changes between snapshots
snapper -c root diff <N1>..<N2>           # Diff files between snapshots
snapper -c root undochange <N1>..<N2>     # Revert changes
snapper -c root cleanup number            # Run cleanup algorithm

# ─── LIMINE ──────────────────────────────────────────────
limine-update                             # Regenerate boot entries
limine-snapper-restore                    # Restore from booted snapshot
systemctl status limine-snapper-sync      # Check sync service

# ─── MKINITCPIO ──────────────────────────────────────────
mkinitcpio -P                             # Rebuild all presets
mkinitcpio -p linux                       # Rebuild specific preset
lsinitcpio /boot/initramfs-linux.img      # List initramfs contents
lsinitcpio -a /boot/initramfs-linux.img   # Analyze initramfs

# ─── LUKS ────────────────────────────────────────────────
cryptsetup open /dev/sdX2 cryptroot       # Open LUKS
cryptsetup close cryptroot                # Close LUKS
cryptsetup luksDump /dev/sdX2             # Show LUKS header info

# ─── EFI ─────────────────────────────────────────────────
efibootmgr                               # List EFI boot entries
efibootmgr -b XXXX -B                    # Delete entry XXXX
efibootmgr --bootorder 0001,0002         # Set boot order
```

---

## Appendix C — Troubleshooting

### Problem: Snapper says `.snapshots is not a subvolume`

```bash
# The auto-created subvolume conflicts with your dedicated one
sudo umount /.snapshots
sudo btrfs subvolume delete /.snapshots
sudo mkdir /.snapshots
sudo mount -a
sudo chmod 750 /.snapshots
```

### Problem: `limine-update` produces no boot entries

Check:
1. `/etc/default/limine` exists and has a valid `CMDLINE`
2. Kernel images exist at `/boot/vmlinuz-*`
3. Initramfs images exist at `/boot/initramfs-*.img`
4. Run `limine-update` with any verbose flags available

### Problem: Can't boot — LUKS prompt doesn't appear

Your mkinitcpio hooks are wrong. Boot from live USB and fix:

```bash
cryptsetup open /dev/sda2 cryptroot
mount -o subvol=@ /dev/mapper/cryptroot /mnt
mount /dev/sda1 /mnt/boot
arch-chroot /mnt

# Fix hooks
nano /etc/mkinitcpio.conf.d/hooks.conf
# Ensure: HOOKS=(base udev keyboard autodetect microcode modconf kms keymap consolefont block encrypt filesystems fsck)

mkinitcpio -P
exit
umount -R /mnt
reboot
```

### Problem: Booted from snapshot but `limine-snapper-restore` not found

Install the package (may require network):

```bash
paru -S limine-snapper-sync
```

Or do a manual rollback (see [[#Part 10 — Emergency Manual Rollback]]).

### Problem: Snapshots consuming too much space

```bash
# Check space
sudo btrfs filesystem usage /
sudo btrfs qgroup show / --raw

# Force cleanup
sudo snapper -c root cleanup number
sudo snapper -c home cleanup number

# Or manually delete old snapshots
sudo snapper -c root list
sudo snapper -c root delete 1 2 3
```

### Problem: `btrfs quota` errors or slow performance

```bash
# Disable quotas if causing issues
sudo btrfs quota disable /

# Note: Snapper's SPACE_LIMIT and FREE_LIMIT won't work without quotas
# You'll rely on NUMBER_LIMIT only for cleanup
```

### Problem: Kernel updated but Limine config not refreshed

```bash
# Manually trigger the update
sudo mkinitcpio -P
sudo limine-update
```

Check that the pacman hooks are in place:

```bash
ls -la /usr/share/libalpm/hooks/ | grep mkinitcpio
```

---

## Complete Checklist

Use this to verify everything is set up:

- [ ] Limine EFI binary deployed to `/boot/EFI/BOOT/BOOTX64.EFI`
- [ ] Limine registered in EFI boot manager (`efibootmgr`)
- [ ] `/boot/limine.conf` exists with valid boot entries
- [ ] `/etc/default/limine` configured with correct `CMDLINE`
- [ ] `@snapshots` subvolume exists and mounted at `/.snapshots`
- [ ] `@home_snapshots` subvolume exists and mounted at `/home/.snapshots`
- [ ] Both snapshot mounts in `/etc/fstab`
- [ ] Snapper config `root` created (`snapper list-configs`)
- [ ] Snapper config `home` created
- [ ] Snapper cleanup limits configured
- [ ] BTRFS quotas enabled (`btrfs quota enable /`)
- [ ] `snapper-cleanup.timer` enabled
- [ ] mkinitcpio hooks include `encrypt` (and `btrfs-overlayfs` if installed)
- [ ] Initramfs rebuilt (`mkinitcpio -P`)
- [ ] `limine-snapper-sync.service` enabled
- [ ] `limine-update` ran successfully with boot entries generated
- [ ] `snap-pac` installed (optional)
- [ ] Test snapshot created and visible in `snapper list`
- [ ] Test reboot successful with Limine menu

---

> [!quote] References
> - [Arch Wiki — Btrfs Snapshots](https://wiki.archlinux.org/title/Btrfs#Snapshots)
> - [Arch Wiki — Snapper](https://wiki.archlinux.org/title/Snapper)
> - [Arch Wiki — mkinitcpio](https://wiki.archlinux.org/title/Mkinitcpio)
> - [Arch Wiki — dm-crypt/Encrypting an entire system](https://wiki.archlinux.org/title/Dm-crypt/Encrypting_an_entire_system)
> - [CachyOS Wiki — BTRFS Snapshots](https://wiki.cachyos.org/configuration/btrfs_snapshots/)
> - [Limine Bootloader](https://limine-bootloader.org/)
