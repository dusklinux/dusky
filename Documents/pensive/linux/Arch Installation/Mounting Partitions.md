With the Btrfs subvolumes created and the top-level volume unmounted, the next step is to mount the subvolumes and the EFI System Partition (ESP) to their correct locations. This prepares the directory structure for the Arch Linux installation.

### 1. Mount the Root Subvolume (`@`)

First, mount the `@` subvolume to `/mnt`. This directory will become the root (`/`) of your new system. This example uses compression and avoids access-time writes; the trade-offs depend on the workload.

> [!WARNING]
> Replace `/dev/root_partition` with your actual Btrfs partition (e.g., `/dev/nvme0n1p2` or `/dev/sda2`).

```bash
mount -o noatime,compress=zstd:3,subvol=@ /dev/root_partition /mnt
```

> [!NOTE] Mount options
> `noatime` avoids access-time updates; software that needs them may behave differently. `compress=zstd:3` enables transparent compression, which can reduce I/O but costs CPU and does not benefit every workload. `subvol=@` selects the root subvolume. Btrfs chooses SSD behavior and discard defaults according to the device and kernel; do not force SSD-specific options on unknown hardware. Most Btrfs mount options apply to the entire filesystem, and the first mounted subvolume determines them. See [Btrfs mount options](https://btrfs.readthedocs.io/en/latest/ch-mount-options.html).

### 2. Create Mount Points for Home and Boot

Before mounting the remaining partitions, you must create the directories within `/mnt` that will serve as their mount points.

```bash
mkdir /mnt/home
mkdir /mnt/boot
```

You can verify their creation by listing the contents of `/mnt`:

```bash
ls /mnt
```

### 3. Mount the Home Subvolume (`@home`)

Next, mount the `@home` subvolume to the `/mnt/home` directory you just created. Use the same filesystem-wide options as the root mount.

> [!NOTE]
> Use the same Btrfs partition device as you did for the root subvolume.

```bash
mount -o noatime,compress=zstd:3,subvol=@home /dev/root_partition /mnt/home
```

### 4. Mount the EFI System Partition (ESP)

Finally, mount your boot partition (ESP) to `/mnt/boot`.

> [!IMPORTANT]
> This step must be done **after** mounting the root filesystem to `/mnt`. The bootloader paritition requires the boot directory to be created inside the mounted root partition .
>
> Be sure to replace `/dev/esp_partition` with your actual EFI partition (e.g., `/dev/nvme0n1p1` or `/dev/sda1`).

```bash
mount /dev/esp_partition /mnt/boot
```

