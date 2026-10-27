### Creating a Swap Partition (During Arch Install)

These steps can be performed as root inside the target chroot after partitioning. This creates a swap signature and a target fstab entry; it does not activate swap in the live environment. If `genfstab` has already included the intended swap, do not add a duplicate entry.

#### Step 1: Format the Swap Partition

First, you must format the designated partition (e.g., `/dev/sdXN`) as a swap area using the `mkswap` utility.

> [!WARNING] Data Loss
> This command will permanently erase all data on the specified partition. Double-check that you have selected the correct device.
```bash
lsblk
```
```bash
mkswap /dev/sdXN
```
*Replace `/dev/sdXN` with your actual swap partition, for example, `/dev/sda3`.*

#### Step 2: Enable Swap at Boot via `fstab`

To ensure the system automatically uses this swap space on every boot, you must add an entry to the file system table (`/etc/fstab`).

1.  **Find the UUID of your swap partition.** The `lsblk -f` command is the most straightforward way to list all partitions with their corresponding UUIDs.

    ```bash
    lsblk -f
    ```
    **Example Output:**
    ```
    NAME        FSTYPE      FSVER    LABEL       UUID                                 FSAVAIL FSUSE% MOUNTPOINTS
    ...
    └─sda3      swap        1                    a1b2c3d4-e5f6-7890-abcd-1234567890ef                [SWAP]
    ...
    ```
    Copy the UUID of your swap partition.

2.  **Add the entry to `/etc/fstab`.** Open the file with a text editor (e.g., `nano /etc/fstab`) and add the following line, replacing `_your_swap_uuid_` with the UUID you just copied.

    ```
    # /etc/fstab: static file system information.
    #
    # <file system> <dir> <type> <options> <dump> <pass>
    UUID=_your_swap_uuid_ none swap defaults 0 0
    ```

> [!TIP] No `swapon` Needed in Chroot
> You do not need to run `swapon` inside the chroot environment. The `systemd` init system will automatically read the `/etc/fstab` file on the first boot and activate the swap partition.
