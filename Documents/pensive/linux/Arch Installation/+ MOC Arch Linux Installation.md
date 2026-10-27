> [!WARNING] Review status
> These notes contain several overlapping older installation recipes. The systemd-boot, Btrfs mounting, base-package and mkinitcpio notes have been revised, but the remaining guides and speedruns have not yet been fully checked. Do not combine commands from different recipes without checking their assumptions. The offline installer has a different, more elaborate layout. Verify the final ISO's package versions and repository configuration before installation.


This master note outlines the complete, step-by-step process for a manual Arch Linux installation. The guide is divided into three main phases: pre-installation setup, core system installation, and post-installation configuration.

---

## Phase 1: Pre-Installation Setup

Create the [[Create a Bootable USB Drive|installation medium]] first, then boot it in UEFI mode. These steps are performed within the live installation environment to improve usability and prepare the system before beginning the core installation.

> [!NOTE] Live Environment Preparation
> - [ ] [[Increase Console Font Size]]: Improve readability on high-resolution displays.
> - [ ] [[Set Keyboard Layout]]: Configure the correct keyboard map for your language.
> - [ ] [[Verify Boot Mode]]: Verify the installation medium booted in UEFI mode.
> - [ ] [[SSH]]: Set up remote access for a more comfortable installation experience.
> - [ ] [[Limiting Battery Charge]]: (ASUS Laptops) Temporarily limit the charge threshold to preserve battery health.
> - [ ] [[Initialize Pacman Keyring]]: Check the live keyring and the ISO repository requirements.

---

## Phase 2: Core Installation

Follow these steps sequentially to partition the disk, install the base system, and configure it.

### A. Environment and Networking
- [ ] [[IWD]]: Optional for an online installation.
- [ ] [[Synchronize the System Clock]]: Check the clock, with or without a network.

### B. Disk Preparation
- [ ] [[Disk Partitioning]]
- [ ] [[Formatting Partitions]]
- [ ] [[Btrfs Subvolume Creation]]
    - [[Unmount Subvolumes]]
- [ ] [[Mounting Partitions]]

### C. Base System Installation
- [ ] [[Synchronize Pacman Mirrors]]: Online installations only; use the ISO repository configuration offline.
- [ ] [[Install Kernel and Base Packages]]
- [ ] [[Generate the fstab File]]
- [ ] [[Chroot into the New System]]

### D. System Configuration (Inside Chroot)
- [ ] *Optional* [[Optimizing fstab Entries]]
- [ ] [[Setting Time Zone]]
- [ ] [[Configure System Locale]]
- [ ] [[Setting Hostname]]
    - - [ ] *Optional:* [[Configure the Hosts File]]
- [ ] [[Setting Root Password]]
- [ ] [[User Account Creation]]
    - - [ ] *Optional:* [[User Group Assignments]]
- [ ] [[Configuring Sudo Privileges]]
- [ ] *Optional* [[Reflector Setup (mirror)]]
- [ ] [[Package Installation]]
- [ ] [[Configuring the Initial RAM File System]]
- [ ] [[Generating the Initramfs]]
- [ ] Choose a boot loader. For the documented simple Btrfs layout, see [[Boot Loader systemd boot]]. [[GRUB]] is an alternative and has not yet been fully reviewed.
- [ ] *Optional* [[ZRAM Setup]]
- [ ] *Optional* [[Disk Swap]]
- [ ] [[Enabling System Services]]: Select services appropriate to the installed packages and machine; this older note has not yet been fully reviewed.

### E. Finalization
- [ ] *Optional* [[Optional steps arch install]]
- [ ] [[Exiting and Unmounting]]

---

## Phase 3: Post-Installation

Once you have rebooted into your new Arch Linux system, perform these final steps to install an AUR helper and additional software.

1. [[Installing an AUR Helper]]
2. [[AUR Packages]]
    - [[AUR Package services]]

---

## Reference Material
- [[Key Configuration Files in Arch Linux]]
