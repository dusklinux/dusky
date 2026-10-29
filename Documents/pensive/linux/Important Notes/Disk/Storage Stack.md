# Storage Stack — How a Byte Reaches the Disk

> [!abstract] Start here
> Learn the layers first, then use [[Disk]] for routine operations, [[Mounting and fstab]] for mounts, [[BTRFS]] for Btrfs, [[Bitlocker]] for Windows encryption, and [[Fixing Un Mountable NTFS drive]] for NTFS failures. [[Disk Verification]] records what was actually checked on this system on **2026-10-29**.

## A path is the top of a stack

A filename such as `$HOME/Documents/example.txt` is resolved through the process's mount namespace. The filesystem translates the file's extents into block addresses. Lower layers may translate those addresses again before the hardware receives an I/O request.

```text
application opens /mnt/data/example.txt
                  │
VFS + mount table │ which mounted filesystem contains this path?
                  ▼
filesystem        ext4 / Btrfs / NTFS / exFAT
                  │ files, directories, metadata, free space
                  ▼
optional mapping  dm-crypt / LVM / MD RAID
                  │ encryption / logical extents / redundancy
                  ▼
partition         a bounded range of sectors described by GPT
                  ▼
block device      NVMe namespace / SATA disk / USB-attached disk
                  ▼
controller        translates host requests to the physical medium
```

Layers are optional. Btrfs can use a whole device directly. A loop device can expose an image file as a block device. Btrfs can manage multiple devices without LVM or MD. The diagram is a common layout, not a required layout.

| Layer | What it manages | Inspect it with | Change it with |
|---|---|---|---|
| Physical device/controller | Capacity, firmware, errors, power states | `lsblk`, `smartctl`, `nvme` | Device-specific tools |
| Partition table | Sector ranges and partition identifiers | `fdisk -l`, `sfdisk --dump` | `fdisk`, `sfdisk`, `parted` |
| Encryption mapping | Decrypted view of encrypted sectors | `cryptsetup status`, `lsblk` | `cryptsetup open` / `close` |
| Filesystem | Files, directories, allocation, metadata | `findmnt`, `df`, filesystem tools | `mkfs.*`, filesystem tools |
| Btrfs subvolume | Independently snapshotable file tree | `btrfs subvolume list` | `btrfs subvolume ...` |
| Mount | Attaches a tree at a pathname | `findmnt`, `/proc/self/mountinfo` | `mount`, `umount`, systemd |

**Choose the tool for the layer.** An NTFS checker needs the NTFS volume. If BitLocker surrounds it, unlock the container first and give the checker the decrypted mapper. Formatting the outer partition would overwrite the encrypted container instead.

## Disk, partition, filesystem, and mount point are different

A **disk** is the addressable block device. A **partition** is a range within it. A **filesystem** is a data structure stored on a partition or another block device. A **mount point** is the directory where that filesystem becomes visible.

Creating a directory does not mount anything. If `/mnt/data` exists while its intended drive is absent, writing there can fill the filesystem that contains the directory. Verify mounts before bulk copies:

```bash
findmnt --mountpoint /mnt/data
findmnt --target /mnt/data
```

`--mountpoint` asks whether the path is an exact mount point. `--target` asks which mount contains a path, so it can correctly return `/` when `/mnt/data` is an ordinary directory.

Mounting over a populated directory hides its underlying contents until unmount. It does not merge the directories or delete the hidden files.

## Read the device names

| Example | Meaning |
|---|---|
| `/dev/nvme0` | NVMe controller character device; used by many administration commands |
| `/dev/nvme0n1` | NVMe namespace block device |
| `/dev/nvme0n1p3` | Partition 3 of that namespace |
| `/dev/sda`, `/dev/sda2` | SCSI-style disk and partition names; also used by SATA and USB storage |
| `/dev/mmcblk0p1` | Partition on an SD/eMMC block device |
| `/dev/dm-0` | Kernel-assigned device-mapper node |
| `/dev/mapper/media` | Named mapping, for example one created with `cryptsetup open ... media` |
| `/dev/loop0` | Block-device view of a file |
| `/dev/zram0` | Compressed RAM-backed block device; can contain swap or a filesystem |

Numbers and letters are discovery names, not permanent hardware identities. NVMe multipath can introduce additional naming distinctions; inspect `nvme list-subsys` rather than inferring an entire topology from a name.

For a mapper, prefer the name chosen at creation or the name reported by the unlocking tool. UDisks often uses `luks-...` or `bitlk-...`; `/etc/crypttab` uses its first field. There is no universal mandatory `luks-UUID` name.

## Persistent naming

`/dev/disk/` links are normally maintained by **udev**, not created as permanent identities by the kernel itself.

| Identifier | Identifies | What can change it? | Useful for |
|---|---|---|---|
| `UUID=` / `by-uuid` | Filesystem or encrypted-container signature | Reformatting, explicit UUID change | Filesystem mounts; outer UUID in crypttab |
| `PARTUUID=` / `by-partuuid` | Partition-table identity | Recreating/changing the partition entry | Locating a partition independently of its filesystem |
| `by-id` | Device identity exposed by hardware/bridge | Different bridge, firmware, missing/duplicated serials | Whole-disk operations |
| `LABEL=` / `by-label` | Human-assigned filesystem label | Relabeling; another volume can share the label | Convenient recognizable names |
| `by-path` | Connection topology | Moving ports/controllers | A particular connector |

```bash
lsblk -o NAME,PATH,TYPE,FSTYPE,LABEL,UUID,PARTUUID,MOUNTPOINTS
ls -l /dev/disk/by-id /dev/disk/by-uuid /dev/disk/by-partuuid
```

A LUKS volume has an **outer LUKS UUID**. Its decrypted filesystem usually has a different **inner filesystem UUID**. Use the outer UUID to unlock; use the inner UUID or named mapper to mount.

Cloning a disk can duplicate filesystem UUIDs, partition UUIDs, and labels. Persistent does not mean globally unique after copying. Connect clones carefully and verify which underlying device each link resolves to.

## Sectors, blocks, alignment, and units

A sector is a block-device addressing unit. A filesystem block is a filesystem allocation/checksum unit. Neither is necessarily the device's internal NAND page or erase-block size.

```bash
lsblk -d -o NAME,SIZE,LOG-SEC,PHY-SEC,MIN-IO,OPT-IO,ROTA,TRAN
```

| Unit | Exact bytes |
|---|---:|
| kB / MB / GB / TB | 1,000 / 1,000,000 / 1,000,000,000 / 1,000,000,000,000 |
| KiB / MiB / GiB / TiB | 1,024 / 1,048,576 / 1,073,741,824 / 1,099,511,627,776 |

A marketed 1 TB drive is about 931 GiB before filesystem overhead. That difference is primarily a unit conversion.

GPT stores LBAs in the device's logical-sector units. Linux sysfs `start` and `size` block-device attributes use **512-byte sectors**, even on devices whose logical sectors are larger. A resize script must distinguish these units; blindly multiplying every sector count by the same value is incorrect.

Current partitioning tools normally choose suitable alignment. Preserve the original partition start when resizing: changing the start changes which bytes the filesystem sees as its beginning.

## GPT and the ESP

Use GPT for a new Arch installation on the supported UEFI platform. GPT has a primary and backup header/table with CRC checks. These help detect and recover partition-table damage; they do not checksum filesystem contents or replace a backup.

The often-quoted MBR 2 TiB limit assumes 512-byte logical sectors. GPT's address range and partition count depend on sector size and table layout; “128 partitions” is a common default, not an intrinsic maximum.

The **EFI System Partition (ESP)** is identified by its GPT type and normally contains a FAT filesystem. Its mount point can be `/boot`, `/efi`, or another installation-specific path. FAT32 is also a general-purpose filesystem; it is not restricted to ESPs. A FAT32 filesystem alone does not set the GPT ESP type.

## Mounts are a runtime tree

One Btrfs filesystem can be mounted several times with different subvolumes. These mounts share one allocation pool; their `df` free-space numbers are not independent capacities.

A Btrfs subvolume is not a disk partition. Its files can share extents with snapshots and reflinks. Separately mounting it does not reserve a fixed amount of disk space.

A **bind mount** exposes an existing directory tree at another path; it does not make another copy of the files:

```bash
sudo mount --bind /srv/data /mnt/data-view
sudo umount /mnt/data-view
```

Processes can have different mount namespaces. A container's or service's mount view can differ from your terminal's. `/proc/self/mountinfo` describes the calling process's view.

## From power button to mounted root

For a typical systemd-boot Arch setup:

1. UEFI executes a bootloader from the ESP.
2. A boot entry selects a kernel/initramfs or a unified kernel image (UKI), plus its command line.
3. Early userspace loads the needed drivers and assembles/unlocks the root device.
4. The root filesystem is mounted; `rootflags=subvol=/@` can select a Btrfs root subvolume explicitly.
5. The system switches into the installed root. systemd generates mount/swap units from fstab and encryption units from crypttab.
6. Other filesystems mount at boot, on first access, or after a later explicit unlock.

```bash
bootctl status --no-pager
cat /proc/cmdline
findmnt --mountpoint /
```

A configured `subvol=`/`subvolid=` overrides the Btrfs default-subvolume choice for that mount. `btrfs subvolume set-default` therefore does not automatically switch a system booting with explicit `rootflags=subvol=/@`.

The exact initramfs setup matters for encrypted root and hibernation. Read the active boot configuration; do not copy an unrelated system's root UUID, resume offset, or hook list.

## Resize from the correct side

**Grow outside-in; shrink inside-out.** Each inner layer must fit inside the layer containing it.

```text
grow:   partition → encryption/LVM mapping → filesystem
shrink: filesystem → encryption/LVM mapping → partition
```

Only include layers actually present. Resize support and the required online/offline state are filesystem-specific. Free space in `df` is not proof that an arbitrary shrink target is valid. See [[BTRFS#Resizing]].

## Diagnose the first failing layer

| Observation | Next check |
|---|---|
| Device absent from `lsblk` | Power, cabling, USB/PCIe/controller logs |
| Device present, expected partition absent | Partition table and the actual target device |
| Encrypted partition visible, mapper absent | Unlock operation and encryption credentials/support |
| Mapper exists, filesystem probe fails | Probe the correct mapper; inspect logs and container state |
| Filesystem recognized, mount fails | Driver, exact mount options, filesystem condition, kernel log |
| Mount works, application fails | Path, ownership, permissions, free space, read-only state |

Recognizing a filesystem signature proves only that a recognizable signature exists. Unlocking a mapper proves a mapping was created. Neither observation proves that all data is intact.

## Further reading

Installed references: `lsblk(8)`, `findmnt(8)`, `mount(8)`, `fstab(5)`, `sfdisk(8)`, `cryptsetup(8)`, and `btrfs(5)`. Prefer the installed manual for exact behavior on this installation. Use [[Disk Verification]] to distinguish verified host observations from examples.
