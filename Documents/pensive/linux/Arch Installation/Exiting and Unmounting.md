
This is the final step within the live installation environment. After configuring the new system from within the `chroot` jail, you must exit it and cleanly unmount the partitions before rebooting into your new Arch Linux installation.

## 1. Exit the `chroot` Environment

First, exit from the `chroot` environment to return to the shell of the live installation media.

```bash
exit
```

## 2. Unmount All Partitions

Next, unmount all the partitions you previously mounted under `/mnt`. The `-R` (recursive) option ensures that all nested mount points (like `/mnt/boot` and `/mnt/home`) are unmounted correctly.

```bash
umount -R /mnt
```

> [!NOTE] Correct Spelling
> Be careful to use the command `umount` (without an "n"). A common typo is `unmount`, which will result in an error.

> [!WARNING] Data Integrity
> Unmounting explicitly confirms that the filesystems are no longer busy before reboot. If unmounting fails, inspect the error and resolve the processes or mounts keeping them busy.

After successfully unmounting the partitions, you are now ready to restart your machine. You can do this with the `shutdown now` command.

```bash
shutdown now
```

Upon shuttingdown, remember to remove the installation media (USB drive) to ensure you boot into your newly installed Arch Linux system.

