
This step creates the file system table (`fstab`), which instructs the operating system on how to mount your partitions automatically at boot time.

### Generate fstab

First verify the intended root, home and ESP mounts with `findmnt -R /mnt`. Then use the `genfstab` script to scan your currently mounted partitions and generate the configuration. We use the `-U` flag to identify partitions by their UUIDs, which is more reliable than device names (e.g., `/dev/nvme0n1p1`) that can sometimes change.

```bash
genfstab -U /mnt >> /mnt/etc/fstab
```

> [!TIP] What does this command do?
> - **`genfstab -U`**: Generates `fstab` entries using **U**UIDs for disk identification.
> - **`/mnt`**: Scans the directory where your new system's partitions are mounted.
> - **`>> /mnt/etc/fstab`**: Appends the generated output to the `fstab` file inside your new system.

The installed `genfstab` also examines active swap areas, excluding zram. Without a prefix filter it can include a live environment's active disk swap, even if it is not intended for the installed system. Inspect `swapon --show` and remove any unwanted swap entries from the target file; add an intended target swap entry if necessary. Archinstall uses `-f` to filter by a path prefix, but a target swap **partition** normally appears as `/dev/...` and will not match `/mnt`; do not assume that filter includes it.

The append operation is intended to be run once. Running it again appends duplicate entries; inspect and edit the target file rather than blindly repeating it. Check the command output and exit status.

### Verify the Generated File

It is **highly recommended** to check the generated `fstab` file for any errors before proceeding. An incorrect `fstab` can prevent your system from booting correctly.

You can display the contents of the new file with this command:

```bash
cat /mnt/etc/fstab
```

> [!NOTE] What to Check
> - Ensure all your partitions (root, boot, home, etc.) are listed.
> - Check that the mount points and filesystem types are correct.
> - You can compare the output with the example in [[fstab reference]].
>
> Compare entries with the actual layout. An example fstab from another machine is not a template for device UUIDs or mounts.

