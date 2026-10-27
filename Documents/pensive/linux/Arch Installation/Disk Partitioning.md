
This step involves preparing your storage drive by creating the necessary partitions for Arch Linux. We will create two essential partitions: one for the EFI System (ESP) and another for the root filesystem.

### 1. Identify the Target Disk

First, list all available block devices to identify the disk you intend to install Arch Linux on.

> [!NOTE] Identifying Your Disk
> Pay close attention to the `SIZE` column to distinguish your target drive from other connected devices (like the USB installation media). Common disk names are `/dev/sda`, `/dev/sdb` for SATA drives, or `/dev/nvme0n1` for NVMe drives.

```bash
lsblk
```

### 2. Partition the Disk

We will use `cfdisk`, a user-friendly, cursor-based partitioning tool. Replace `/dev/sdX` with your target disk's identifier from the previous step.

> [!WARNING] Data Loss
> The following steps will erase all data on the selected disk. Double-check that you have selected the correct device.

```bash
cfdisk /dev/sdX
```

Once `cfdisk` launches:
1.  If the disk has no recognized partition table, select **`gpt`** when prompted. If it already has a partition table, `cfdisk` opens it instead; inspect it and decide what to preserve. This example assumes a new or intentionally cleared GPT layout.
2.  Create the following two partitions.

#### Recommended Partition Scheme

| Partition | Type | Size | Purpose |
|---|---|---|---|
| EFI System | `EFI System` | `1G` example | EFI boot files; allow more space if storing multiple kernels and initramfs images |
| Root | `Linux filesystem` | Remainder of disk, sized for your packages and data | The base of your operating system |

> [!TIP] Using `cfdisk`
> - Use the arrow keys to navigate.
> - Select `[ New ]` to create a partition from free space.
> - Enter the size (e.g., `1G`).
> - Use the `[ Type ]` menu to set the partition type.
> - Once you have created both partitions, select `[ Write ]`, confirm with `yes`, and then select `[ Quit ]`.

### 3. Verify the New Partition Layout

After exiting `cfdisk`, verify that the partitions were created correctly by listing the blocks on your target device again.

```bash
lsblk /dev/sdX
```

Your output should now show two new partitions. For example, if your disk is `/dev/nvme0n1`, you should see something similar to this:

```
NAME        MAJ:MIN RM   SIZE RO TYPE MOUNTPOINTS
nvme0n1     259:0    0 476.9G  0 disk 
├─nvme0n1p1 259:1    0     1G  0 part 
└─nvme0n1p2 259:2    0   475G  0 part 
```

Here, `nvme0n1p1` is your EFI partition and `nvme0n1p2` is your root partition. With the disk now partitioned, the next step is to format these partitions.
