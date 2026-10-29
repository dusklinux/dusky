# Disk Management, Benchmarking, Health Monitoring & NVMe Diagnostics

> [!abstract] How to use this reference
> Start with [[Storage Stack]] if disk/partition/filesystem/mapper names are unclear. Use [[Mounting and fstab]] for mounting, [[BTRFS]] for Btrfs policy and recovery, [[Bitlocker]] for Windows encryption, and [[Fixing Un Mountable NTFS drive]] for NTFS triage. [[Disk Verification]] records the actual system, tests, and remaining limits as of **2026-10-29**.

## Identify first; act at the correct layer

```bash
lsblk -o NAME,PATH,SIZE,TYPE,FSTYPE,FSVER,LABEL,UUID,MOUNTPOINTS,MODEL,ROTA,TRAN
findmnt -o TARGET,SOURCE,FSTYPE,OPTIONS
sudo blkid
```

Use explicit columns in scripts; default command output can change. `lsblk --json --output ...` supplies structured output. `lsblk -e7` hides loop devices, which is convenient for a physical-drive overview but inappropriate when investigating an image-backed filesystem.

| Question | Read-only command |
|---|---|
| Exact filesystem mounted here? | `findmnt --mountpoint /mnt/data` |
| Which filesystem contains this path? | `findmnt --target "$HOME/Documents"` |
| Partition boundaries and sector sizes? | `sudo fdisk -l` |
| Save/read a partition table as text? | `sudo sfdisk --dump /dev/disk/by-id/REPLACE_WITH_DISK_ID` |
| Existing signatures on a device? | `sudo wipefs --no-act /dev/disk/by-id/REPLACE_WITH_DISK_ID` |
| NVMe device inventory? | `sudo nvme list` |
| NVMe controller/namespace relationships? | `sudo nvme list-subsys` |
| PCI storage controllers? | `lspci -nn` |
| Logical/physical sector sizes? | `lsblk -d -o NAME,SIZE,LOG-SEC,PHY-SEC,MIN-IO,OPT-IO,ROTA,TRAN` |

`wipefs --no-act` inspects signatures. `wipefs -a` removes them and is a different operation. Neither formats a filesystem or securely erases all old file contents.

Prefer persistent identifiers for repeatable commands. Match the device's model, size, partition layout, and current mounts before a write-capable operation. The same `/dev/nvme0n1` or `/dev/sda` can refer to different hardware after another boot.

## Packages and installed interfaces

Check what is present before installing or assuming a command exists:

```bash
command -v lsblk btrfs cryptsetup smartctl nvme ntfsfix fio ddrescue
pacman -Q util-linux btrfs-progs cryptsetup smartmontools nvme-cli sysstat
pacman -Qo /usr/bin/ntfsfix
```

| Arch package | Relevant commands |
|---|---|
| `util-linux` | `lsblk`, `blkid`, `findmnt`, `mount`, `umount`, `fdisk`, `sfdisk`, `partx`, `losetup`, `blockdev`, `wipefs`, `fstrim` |
| `btrfs-progs` / `compsize` | `btrfs`, `mkfs.btrfs` / extent compression accounting |
| `e2fsprogs` | `mkfs.ext4`, `e2fsck`, `resize2fs`, `tune2fs`, `lsattr`, `chattr` |
| `dosfstools` / `exfatprogs` | FAT / exFAT creation and checking |
| `ntfsprogs` | Installed NTFS utilities here, including `ntfsfix`, `mkntfs`, `mkfs.ntfs` |
| `ntfs-3g` | Separate optional FUSE NTFS driver; not required for this kernel's native `ntfs` mounts |
| `cryptsetup` / `udisks2` | Low-level encryption / session-integrated drive operations |
| `smartmontools` / `nvme-cli` | SMART and NVMe administration |
| `sysstat` / `fio` | `iostat`, `pidstat` / controlled I/O workloads |
| `ddrescue` | GNU `ddrescue` and `ddrescuelog`; Arch package is **not** `gddrescue` |
| `ncdu` | Interactive disk-usage exploration |
| `pciutils` / `parted` | `lspci` / partitioning and `partprobe` |
| `mdadm` / `lvm2` | MD RAID / LVM, if the actual storage stack uses them |

On a connected Arch installation, installing packages should be part of a consistent full upgrade, for example `sudo pacman -Syu --needed fio ddrescue`. Do not refresh only the sync databases and then selectively upgrade dependencies into an unsupported partial-upgrade state. For an offline ISO, provision a consistent package set ahead of time. This notes review did not install or upgrade system packages.

Run `COMMAND --help` or the installed man page for exact syntax. [[Disk Verification]] lists the tested versions; the not-yet-shipped ISO must still be checked against its final package set.

## Capacity: what is full?

```bash
df -hT
df -i
sudo du -xhd1 / | sort -h
sudo du -xhd1 /home | sort -h
```

`df` describes a filesystem's free/used accounting. `du` walks reachable files and totals their allocated blocks. They answer different questions. They can differ because of open-but-deleted files, snapshots, reflinks, compression, sparse files, metadata, reserved space, hidden files beneath mount points, or an incomplete scan.

`du -x` stays on the relevant device boundary. Separately mounted Btrfs subvolumes and other mounted filesystems may need separate scans. Permission errors mean the result is incomplete; use privileges when a complete system view is needed.

For open-but-deleted files:

```bash
sudo lsof +L1
```

The storage remains allocated until the last open reference is released. Restart/close the relevant application deliberately; deleting the name again cannot release it.

Btrfs needs additional accounting:

```bash
sudo btrfs filesystem usage /
sudo btrfs filesystem du -s /home
```

Do not sum the free space of several subvolume mounts on the same Btrfs filesystem. They share one pool. [[BTRFS#Integrity & Space]] explains allocated versus unallocated space and shared extents.

## Partitioning and formatting

> [!danger] These examples replace data structures
> `fdisk` changes partition tables when you write the result. `mkfs.*` creates a new filesystem and destroys the previous filesystem's usability. The examples are procedures to adapt, not commands that were run against your real drives.

### Inspect and save the current layout

```bash
disk='/dev/disk/by-id/REPLACE_WITH_WHOLE_DISK_ID'
sudo fdisk -l -- "$disk"
sudo sfdisk --dump "$disk"
```

Save a partition-table dump and actual file backups on a different device before a real repartitioning task. A table dump preserves sector boundaries and identifiers; it does not back up file data.

### Create/edit partitions

```bash
sudo fdisk -- "$disk"
```

For a new supported-platform installation, select GPT. Review the proposed starts, sizes, and type GUIDs before writing. A Linux filesystem partition and an ESP have different GPT types. Formatting FAT32 does not set the ESP type for you.

After partition-table changes, the on-disk table and the kernel's partition view must agree:

```bash
sudo partprobe -- "$disk"
lsblk -o NAME,PATH,TYPE,SIZE,START,FSTYPE,MOUNTPOINTS -- "$disk"
```

`partx --update --nr NUMBER "$disk"` can update a selected partition's kernel view. If an in-use layout prevents the update, stop and reconcile it rather than continuing with a stale device size. `udevadm settle` waits for udev processing; it does not make a failed partition update succeed.

### Create one filesystem on the chosen partition

Set `part` to the **partition**, not the whole disk, and choose one applicable command:

```bash
part='/dev/disk/by-id/REPLACE_WITH_PARTITION_ID'

# Alternatives: do not run all of these on the same partition.
sudo mkfs.btrfs -L DATA "$part"
sudo mkfs.ext4 -L DATA "$part"
sudo mkfs.fat -F 32 -n DATA "$part"
sudo mkfs.exfat -L DATA "$part"
sudo mkntfs --quick -L DATA "$part"
```

A quick format is not a full-media health test or secure erase. `mkfs.btrfs` defaults include current on-disk features; check rescue-media support for those features instead of assuming an old ISO can mount a newly formatted volume.

### Choosing a filesystem

| Need | Reasonable starting point | Main tradeoff |
|---|---|---|
| Arch root/home with snapshots and compression | Btrfs | Learn allocation, snapshots, and CoW policy |
| Straightforward Linux data filesystem | ext4 | No native Btrfs-style snapshots/compression |
| Windows-managed NTFS data | NTFS with the verified native driver | Use filesystem-aware Windows repair when necessary |
| Portable Linux/Windows removable storage | exFAT when its features fit | No ordinary Unix ownership model or journaling guarantees |
| UEFI ESP | FAT32 with ESP partition type | Specialized boot role; limited filesystem features |

There is no single “fastest filesystem” for every file size, access pattern, medium, and durability requirement.

## I/O monitoring

Run these in separate terminals; stop continuous displays with Ctrl+C:

```bash
iostat -dx -y 1
pidstat -d 1
```

`iostat -y` omits the initial since-boot report, making interval samples easier to interpret. Add a final count for a finite measurement, such as `iostat -dx -y 1 5`.

| Column | Interpretation |
|---|---|
| `r/s`, `w/s`, `d/s`, `f/s` | Requests per second by operation |
| `rkB/s`, `wkB/s` | Read/write throughput in the selected report units |
| `r_await`, `w_await` | Average request latency, including queueing, in ms |
| `aqu-sz` | Average number of outstanding requests |
| `%util` | Fraction of interval with outstanding I/O; not a universal device saturation percentage |

NVMe serves requests in parallel. `100% util` does not prove all controller capacity is consumed. Compare throughput, latency, queue depth, and the application's response time.

A logical mapping, its partition, and the whole disk can all account for the same I/O at different layers. Adding their counters double-counts traffic. Process write accounting also differs from application bytes and physical NAND writes because of buffering, writeback, metadata, and controller behavior.

## Benchmarking

### Decide what is being measured

Distinguish application workload, filesystem performance, encrypted-stack performance, and device throughput. Queue depth, block size, working-set size, file creation/allocation, compression, checksums, snapshots, temperatures, power policy, and device caches can all change a result.

A brief benchmark cannot establish sustained write speed after an SSD's pseudo-SLC cache is exhausted. Increasing the test size/time adds writes and may fill the volume; choose it for a specific measurement rather than maximizing it indiscriminately.

`O_DIRECT` requests avoidance of the page cache. It does not bypass encryption, filesystem allocation, the SSD's cache, or the controller's translation layer. Alignment and filesystem behavior still matter.

**Btrfs/Linux 7.3 detail:** aligned direct writes to checksummed files use the newer direct-write handling and do not perform transparent compression. A temporary-image test here confirmed buffered zeros compressed while direct zeros did not. Therefore, a direct-I/O Btrfs benchmark does not measure the same write path as a normally buffered compressed application workload. Do not disable checksums or CoW just to improve a benchmark number.

### Reproducible file-based fio example

Install Arch's `fio` package if needed; the intended program is the storage tester (`fio --version` reports `fio-...`). The following uses a new private directory on a **verified mounted data filesystem**. It writes about 4 GiB during initialization and more during the mixed workload.

```bash
# Verify this is the intended mounted storage before proceeding.
findmnt --mountpoint /mnt/data
bench_dir=$(mktemp -d -- /mnt/data/.fio-XXXXXXXX)
bench_file="$bench_dir/fio.bin"

# Allocate/populate the whole test file before measuring reads or mixed I/O.
fio --name=seqwrite --filename="$bench_file" --size=4G \
    --rw=write --bs=1M --ioengine=io_uring --direct=1 --iodepth=16 \
    --refill_buffers=1 --randrepeat=0 --group_reporting --fsync_on_close=1

# Read only an existing initialized file; do not manufacture an empty read test.
fio --readonly --name=seqread --filename="$bench_file" --size=4G \
    --rw=read --bs=1M --ioengine=io_uring --direct=1 --iodepth=16 \
    --allow_file_create=0 --group_reporting

# 70% reads / 30% writes, 4 KiB requests, queue depth up to 32.
fio --name=randrw --filename="$bench_file" --size=4G \
    --rw=randrw --rwmixread=70 --bs=4k --ioengine=io_uring \
    --direct=1 --iodepth=32 --time_based=1 --runtime=30 \
    --refill_buffers=1 --randrepeat=0 --group_reporting

# Run after all three jobs have finished and results have been saved.
rm -- "$bench_file"
rmdir -- "$bench_dir"
```

The random test depends on the file created by the write test. Do not delete it between jobs. Smaller equivalent jobs exercised all these flags here; the displayed 4 GiB/30 s workload was not run on your physical drives.

Read IOPS/bandwidth together with completion-latency percentiles, especially tail latency. Queue depth 1 approximates a different workload from queue depth 32. A desktop responsiveness measurement often benefits from low queue depth; a throughput test may intentionally use more parallelism.

`--fsync_on_close=1` requests final file synchronization. It is not a per-write durability workload. Test `fsync`/`fdatasync` frequency explicitly when that is what the real application requires.

### Why dd and hdparm are limited

`dd` is useful for byte copying and spot checks, but a single sequential stream provides neither mixed-workload behavior nor latency percentiles. Zero buffers can exercise compression/deduplication shortcuts in some layers; buffered reads can measure RAM. Avoid treating that result as a general hardware rating.

`hdparm -t DEVICE` is a sequential device-read spot check; `hdparm -T` measures cache-related host performance. Use `fio` when the workload needs controlled block size, queue depth, duration, or latency reporting. No raw-device write benchmark belongs on a disk containing data you want to keep.

## Drive Health

### Read reports

```bash
sudo smartctl --scan-open
sudo smartctl -x /dev/disk/by-id/REPLACE_WITH_DISK_ID
```

For an NVMe controller selected from `nvme list`:

```bash
ctrl='/dev/nvme0'   # Example only: identify the actual controller this boot.
sudo smartctl -x "$ctrl"
sudo nvme smart-log "$ctrl" -H
sudo nvme error-log "$ctrl"
```

Read-only SMART/NVMe reports were collected from both installed controllers. No drive self-tests, firmware changes, raw writes, or erase operations were initiated.

For USB bridges, use the device type reported by `smartctl --scan-open`. `-d sat` is appropriate for some SATA bridges, not every USB device. A bridge can hide or mistranslate health information.

### Interpret signals rather than one “PASSED” line

| Signal | Interpretation |
|---|---|
| Rising reallocated/pending/uncorrectable sectors | Investigate media health and preserve data |
| Rising SATA UDMA CRC errors | Often transport/cable errors; inspect the actual path |
| Nonzero NVMe critical-warning bits | Decode the bits and thresholds; several distinct conditions exist |
| Rising NVMe media/data-integrity errors | Investigate genuine device-reported integrity failures |
| NVMe `percentage_used` | Vendor endurance-consumption estimate; may exceed 100, not a timer to failure |
| NVMe unsafe shutdown count | History of shutdown events; not by itself proof of lost/corrupt files |
| NVMe error-log entries | Decode status; unsupported admin requests can create entries too |
| Temperature/throttling counters | Correlate with workload, cooling, and performance |

SMART is not a complete predictor of failure. Zero media errors do not prove a disk cannot fail. Trends and matching kernel/application errors matter.

`smartctl` returns a **bitmask** status. A nonzero exit is not always “the command could not run”: bits can represent recorded health/log conditions. Consult `smartctl(8)` before writing `set -e` health-report wrappers. Preserve report output and the exit status.

### Self-tests: explicit diagnostic operations

For a device that advertises support:

```bash
# SATA/SAS examples:
sudo smartctl -t short /dev/disk/by-id/REPLACE_WITH_DISK_ID
sudo smartctl -l selftest /dev/disk/by-id/REPLACE_WITH_DISK_ID
```

NVMe example:

```bash
sudo nvme id-ctrl "$ctrl" -H
sudo nvme device-self-test "$ctrl" --self-test-code=1
sudo nvme self-test-log "$ctrl"
```

Code `1` requests a short test, `2` an extended test, and `0xf` aborts. Check `nvme device-self-test --help` and controller capabilities. Starting a test is not a passive report. On unstable hardware, preserving data takes priority over adding test reads. These starts were documentation-checked, not executed on the installed drives.

### Background monitoring

```bash
systemctl status smartd.service --no-pager
systemctl cat smartd.service
```

To opt into ongoing monitoring after reviewing `/etc/smartd.conf`:

```bash
sudo systemctl enable --now smartd.service
```

Check its journal and configure an actual notification destination; merely starting the daemon does not establish that the user will receive a warning. This review left service enablement unchanged.

## NVMe Reference

`/dev/nvme0` is the controller; `/dev/nvme0n1` is a namespace; `/dev/nvme0n1p1` is a partition. Administration commands commonly use the controller, while ordinary filesystem I/O uses a namespace/partition. Check each command's accepted device type.

```bash
sudo nvme list
sudo nvme list-subsys
sudo nvme id-ctrl "$ctrl" -H
sudo nvme id-ns /dev/nvme0n1 -H
sudo nvme fw-log "$ctrl"
sudo nvme error-log "$ctrl"
```

`id-ctrl` lists support, not a promise that every operation is appropriate for a mounted device. Firmware update, format, sanitize, namespace-management, and destructive feature changes require a separate device-specific procedure.

### Power Management Layers

| Layer | What it controls | Inspect |
|---|---|---|
| NVMe power-management feature (`0x02`) | Selected controller power-management setting | `nvme get-feature ... --feature-id=0x02 -H` |
| APST (`0x0c`) | Autonomous transitions among NVMe power states | `nvme get-feature ... --feature-id=0x0c -H` |
| PCIe ASPM | PCIe link power states | `lspci -vv`, PCIe policy |
| Runtime PM | Kernel runtime suspend/resume of the PCI device | sysfs `power/control` and `power/runtime_status` |

```bash
sudo nvme get-feature "$ctrl" --feature-id=0x02 -H
sudo nvme get-feature "$ctrl" --feature-id=0x0c -H
cat /sys/module/nvme_core/parameters/default_ps_max_latency_us
cat /sys/module/pcie_aspm/parameters/policy
```

Discover the sysfs path from the chosen controller instead of copying another machine's PCI address:

```bash
ctrl_name=${ctrl##*/}
readlink -f -- "/sys/class/nvme/$ctrl_name/device"
cat "/sys/class/nvme/$ctrl_name/device/power/control"
cat "/sys/class/nvme/$ctrl_name/device/power/runtime_status"
```

A selected power-management feature does not necessarily reveal the instantaneous autonomous state at the moment of every I/O. Firmware, driver quirks, controller capabilities, and platform policy influence behavior. Do not carry the old laptop-specific ASPM claim into another installation without logs proving it.

For reproducible timeout/reset diagnosis, possible **temporary boot-time experiments**, one at a time, include:

```text
nvme_core.default_ps_max_latency_us=0
pcie_aspm=off
```

The first changes the NVMe APST latency policy; the second disables PCIe ASPM. They can increase idle power and reduce battery life. They are not general performance defaults, and this review did not apply them.

## TRIM / Discard

TRIM/discard tells a lower layer that blocks are no longer needed. It does not move files, defragment a filesystem, or guarantee immediate physical erasure.

```bash
lsblk -D
systemctl status fstrim.timer --no-pager
systemctl cat fstrim.timer fstrim.service
```

Nonzero discard limits advertise capability at that block-device layer. They do not by themselves prove that a particular filesystem/mapping/bridge combination successfully trims.

For a supported mounted filesystem:

```bash
# No discard issued; checks the command path and reports its planned work.
sudo fstrim --dry-run --verbose /mnt/data

# Actually issue filesystem discard:
sudo fstrim --verbose /mnt/data
```

To opt into the packaged scheduled service:

```bash
sudo systemctl enable --now fstrim.timer
```

The packaged timer here is weekly. This review exercised `fstrim --dry-run --all --verbose`, not live TRIM, and did not enable the timer.

Btrfs enables asynchronous discard by default on supported devices. ext4 commonly uses periodic `fstrim` instead of continuous `discard`. Do not claim synchronous discard always causes a particular slowdown; measure the workload. Some HDDs, including certain thin/SMR arrangements, also expose useful discard support—“never TRIM an HDD” is not a sound universal rule.

### TRIM Through LUKS

For a manually opened container:

```bash
outer='/dev/disk/by-uuid/REPLACE_WITH_OUTER_LUKS_UUID'
sudo cryptsetup open --allow-discards "$outer" media
sudo cryptsetup status media
```

To persist activation flags in a LUKS2 header during an appropriate open:

```bash
sudo cryptsetup open --allow-discards --persistent "$outer" media
```

This is an alternative open command, not a second open on an already active name. `--persistent` writes LUKS2 metadata. A temporary-image test confirmed the discard flag survived closing and reopening without the flag.

Alternatively configure `discard` explicitly in crypttab:

```text
media UUID=REPLACE_WITH_OUTER_LUKS_UUID none luks,discard
```

**Neither plain `cryptsetup open` nor systemd's default LUKS activation enabled discard in the clean-container tests here.** Existing persistent flags can explain why an already-configured volume has discard enabled. Inspect the actual mapping rather than inferring its settings from how it was opened.

Discard exposes allocation information below encryption. Decide deliberately whether that behavior fits the volume; this is an operational consequence, not a reason to add unrelated configuration changes.

### LUKS performance settings

`no-read-workqueue` and `no-write-workqueue` are documented crypttab options for bypassing dm-crypt's internal workqueues. They can help some workloads and hurt others; a lower scheduling cost is not a measured speedup for every system. Compare the real encrypted workload, latency, CPU use, and power before adopting them.

A 4096-byte encryption sector size is also not a universal tuning rule. It changes mapping/data-format constraints and must fit the device, alignment, filesystem, and intended usage. Do not reformat a working container just because an old note promised a theoretical gain. The current mappings and sector sizes are recorded in [[Disk Verification]].

## Filesystem checks and failure triage

```bash
journalctl -k -b --no-pager
journalctl -k -b -g 'nvme|ata|ahci|AER|I/O error|timeout|reset|CRC|BTRFS|EXT4-fs' --no-pager
```

| Evidence | Investigate |
|---|---|
| Read/write I/O errors or disappearing devices | Media, power, cable, bridge, controller; preserve data |
| USB resets/disconnects | Entire connection/power path, not just the filesystem |
| NVMe resets/timeouts/AER | PCIe link, controller firmware, platform and power policy |
| Filesystem corruption messages | Filesystem state and possible lower-layer cause |
| “Wrong fs type, bad option, bad superblock” | Generic mount failure: inspect the next kernel log messages |

For an **unmounted** ext4 filesystem on a stable device, `e2fsck -fn DEVICE` is a non-writing check. Never interpret a live-filesystem scan as a trustworthy offline consistency result. Repair-capable `e2fsck` must use an unmounted filesystem and a deliberate recovery procedure.

Btrfs scrub and offline `btrfs check` have different purposes; see [[BTRFS#Integrity & Space]]. NTFS repair limits and image-first recovery are in [[Fixing Un Mountable NTFS drive]]. A benchmark is not a recovery tool.

## RAID / LVM Inspection

Only use the tools for layers actually present:

```bash
cat /proc/mdstat
sudo mdadm --detail /dev/md0

# Requires lvm2; these are separate reports.
sudo pvs
sudo vgs
sudo lvs -a -o +devices
```

`/dev/md0` is an example target to identify, not an assumed device. These optional-stack reports were documentation-checked, not exercised against an MD/LVM installation here. RAID provides particular redundancy/availability properties; independent backups are still necessary.

## Local helpers

See [[Local Drive Tools]] for verified entry points, scope, and limitations of `~/user_scripts/drives/`. A script's reassuring name or comments do not prove its behavior. In particular, NTFS dirty-flag clearing and adding a temporary Btrfs device are recovery mutations, not passive health checks.

## References

Installed manuals: `lsblk(8)`, `findmnt(8)`, `sfdisk(8)`, `partx(8)`, `fstrim(8)`, `smartctl(8)`, `iostat(1)`, `pidstat(1)`, `cryptsetup-open(8)`, `crypttab(5)`, `btrfs(5)`, and individual `nvme-*` pages. The fio 3.42 and GNU ddrescue 1.30 package documentation and reduced-size workloads were checked without installing those packages. See [[Disk Verification]] for the evidence and exceptions.
