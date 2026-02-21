regenerating the initramfs images

```bash
sudo limine-mkinitcpio
```


Limine is a modern, advanced, and portable multiprotocol bootloader. It serves as the reference implementation for the Limine boot protocol and supports Linux as well as chainloading other loaders.

#### Pros

- Supports multiple boot protocols, including Multiboot2 and the Linux boot protocol.
- Can boot on both UEFI and BIOS systems.
- Has theming capabilities similar to GRUB.
- Supports Btrfs snapshots via `limine-snapper-sync`, enabled by default on CachyOS with Btrfs.

#### Cons

- `/boot` must use FAT12/16/32 or ISO9660. Other filesystems require additional setup.
- Does not automatically add an entry to UEFI NVRAM. This must be done manually with `efibootmgr`, or handled automatically with `limine-entry-tool` (preinstalled on CachyOS).
- Does not work with UFS (Universal Flash Storage), used e.g. in some Chromebooks.
- TPM PCRs are not measured. [Will fail TPM PCR0 Reconstruction test](https://github.com/fwupd/fwupd/wiki/TPM-PCR0-differs-from-reconstruction).
    - Fixable by booting UKI that uses systemd-stub as UEFI stub. The systemd-ukify can make this ([see here](https://github.com/fwupd/fwupd/discussions/9568)).
    - Another workaround is to chainload another bootloader that measured TPM PCR (e.g. systemd-boot, GRUB).