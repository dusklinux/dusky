 Part 3: Installing GRUB to the EFI System Partition (ESP)

With the packages installed and configured, the next step is to install the GRUB bootloader files to your EFI System Partition (ESP) and create a boot entry in your motherboard's firmware.

### 1. Run the `grub-install` Command

This command installs the necessary files into the `--efi-directory` and creates a boot entry named by `--bootloader-id`.

```bash
grub-install --target=x86_64-efi --efi-directory=/boot --bootloader-id=GRUB
```

> [!IMPORTANT] Verify Your EFI Directory Path
> The `--efi-directory` path is critical. It must point to where your ESP is mounted *inside the chroot environment*.
> - If you mounted your ESP at `/mnt/boot`, the correct path is `/boot`.
> - If you mounted it at `/mnt/boot/efi`, the correct path is `/boot/efi`.
>
> Before running the command, confirm your mount point with `lsblk` or `findmnt`. An incorrect path is a common point of failure.

Here is a breakdown of the command's flags:

| Flag | Description |
| :--- | :--- |
| `--target=x86_64-efi` | Specifies that we are installing for a 64-bit UEFI system. |
| `--efi-directory=/boot` | The mount point of your EFI System Partition (ESP). **Adjust this path as needed.** |
| `--bootloader-id=GRUB` | The name of the bootloader entry in the UEFI boot menu. It also creates a corresponding directory at `/boot/EFI/GRUB`. |

### 2. Verify the Installation

After the command completes successfully, verify that the main GRUB EFI file has been created in the correct location:

```bash
ls /boot/EFI/GRUB/grubx64.efi
```

The file confirms that this EFI binary exists, not that the firmware entry or generated boot menu works. Inspect `efibootmgr -v`, review the command output and continue to [[Generating the Final Grub File]]. Adjust the path for an ESP mounted elsewhere.

***