# GRUB on UEFI

This is an alternative to [[Boot Loader systemd boot]], not an additional required boot loader. Follow [[GRUB]] for the package, configuration, installation, and generated-menu steps. The examples there assume the target system is mounted and commands run inside its chroot.

Before installing, confirm the actual ESP mount point and CPU/firmware architecture. Keep any kernel parameters required by your storage or encryption setup; do not replace the entire command line with a generic tuning profile. Check the resulting EFI files, firmware entry, and generated menu before rebooting. The simple systemd-boot instructions use `/boot` for the ESP, but other layouts require different paths.

The GRUB workflow has not been tested end to end on the final ISO. Consult the [ArchWiki GRUB guide](https://wiki.archlinux.org/title/GRUB) for your actual filesystem and boot layout.
