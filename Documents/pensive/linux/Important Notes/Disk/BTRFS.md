# Btrfs Filesystem Management

> [!abstract] Scope and reading order
> CoW/NOCOW, compression, subvolumes, snapshots, integrity, allocation, ENOSPC, resizing, and swapfiles on Arch/Linux 7.3+. Start with [[Storage Stack]], use [[Mounting and fstab]] for mount syntax, and see [[Disk Verification]] for the exact tested environment and current machine state.

## The model: one pool, many trees

A Btrfs filesystem owns a pool of devices and space. A **subvolume** is an independently managed file tree within that pool. A **snapshot** starts as another subvolume sharing the source's extents. A **reflink** shares file extents between separate inodes.

Sharing avoids immediately copying all the data. When shared data changes, Btrfs allocates a new extent so that other references retain the old contents. Deleting one reference does not free an extent still referenced elsewhere.

| Object | What it isolates | What it does not isolate |
|---|---|---|
| Separate filesystem | Allocation pool, device set, filesystem-wide policy | Hardware failure if on the same device |
| Subvolume | File tree and snapshot boundary | Capacity pool; most filesystem-specific mount options |
| Snapshot | A point-in-time tree of one subvolume | Other subvolumes, independent hardware, application transactions spanning volumes |
| Reflink | Separate filename/inode initially sharing data extents | Initial physical data blocks |

A snapshot on the same drive is useful for undo and rollback. It is not an independent backup against device loss. A root snapshot and a home snapshot taken consecutively are **not one atomic cross-subvolume snapshot**.

## Inspect before changing policy

```bash
findmnt -t btrfs -o TARGET,SOURCE,FSTYPE,OPTIONS
sudo btrfs filesystem show /
sudo btrfs filesystem usage /
sudo btrfs subvolume list -p /
sudo btrfs device stats /
```

Identify the device count, data/metadata profiles, subvolume layout, and effective options. Do not transfer a single-device procedure to a multi-device filesystem without accounting for its profiles and device IDs.

The current root filesystem's verified layout is recorded in [[Disk Verification]]. The reusable examples below deliberately avoid its UUIDs, account name, and transient device numbers.

## CoW and NOCOW

### What each policy changes

Btrfs normally checksums file data and metadata. CoW makes snapshots and shared extents practical. It is a default policy, not proof that every workload must be slow.

| Behavior | Ordinary checksummed CoW file | NOCOW file (`+C`) |
|---|---|---|
| File-data checksums | Yes | No |
| Metadata checksums | Yes | Yes |
| Transparent compression | Available | Disabled |
| Included in a snapshot of its subvolume | Yes | Yes |
| Updates to unshared existing extents | Normally CoW | Can overwrite in place |
| Updates to shared extents | CoW | Must preserve other references; CoW can still occur |
| Reflink | Supported with compatible policy | Supported when source/destination requirements match |

**NOCOW is not snapshot exclusion and does not imply “never CoW.”** Snapshots/reflinks can share its extents. NOCOW also sacrifices file-data checksum validation, so scrub cannot verify that data against a Btrfs checksum.

A temporary-image test confirmed reflinking between two NOCOW files worked. An attempted CoW-to-NOCOW reflink failed. The old blanket statement that NOCOW and reflinks are incompatible was incorrect.

### When to consider NOCOW

VM disks, selected database files, or other large overwrite-heavy workloads can benefit. Measure the actual workload and account for application durability/recovery requirements. Do not disable checksums on all caches, small files, or SSD data by category alone.

Use a separate subvolume when a workload should be outside its parent's snapshots. That boundary excludes the file tree from the parent's snapshot; `+C` controls the data-write policy. These are separate decisions.

### Set the policy before creating files

```bash
# Example: a new subvolume on an already mounted Btrfs filesystem.
sudo btrfs subvolume create /srv/vms
sudo chattr +C /srv/vms
lsattr -d /srv/vms
```

The path must not already be the populated directory you are trying to convert. A directory's `+C` flag is inherited by newly created files. Existing populated files are not reliably converted in place; rewrite them into newly created inodes.

Avoid treating an exact dash pattern in `lsattr` as an interface. Look for `C` in the attribute field; other flags and output width can differ.

### Rename versus copy

| Operation | Result verified here |
|---|---|
| `mv` within one subvolume into a `+C` directory | Same inode; existing CoW state stays CoW |
| GNU `mv` across subvolume boundaries | Rename cannot cross the boundary; copy/delete fallback created a new inode inheriting destination NOCOW |
| `cp -a --reflink=never` into a `+C` directory | New destination file inherited NOCOW |
| `cp --reflink=always` between matching NOCOW files | Worked; extents can be shared |

Cross-subvolume `mv` can therefore turn a cheap rename into a data copy. It is not an atomic migration and can require substantial space/time. `mv --no-copy` is useful when a workflow specifically requires rename-or-fail; it is not a conversion tool.

### Rewrite existing data deliberately

Stop writers first, and make sure source and destination are not active mount points that this rename sequence would try to replace. This example converts an ordinary directory into a separate NOCOW subvolume:

```bash
sudo btrfs subvolume create /srv/vms.nocow
sudo chattr +C /srv/vms.nocow
sudo cp -a --reflink=never --sparse=always /srv/vms/. /srv/vms.nocow/
sudo find /srv/vms.nocow -maxdepth 2 -type f -exec lsattr -- {} +

# After verifying contents, ownership, attributes, and application requirements:
sudo mv -- /srv/vms /srv/vms.old
sudo mv -- /srv/vms.nocow /srv/vms
```

`--reflink=never` requests a genuine rewrite; `--sparse=always` allows holes where appropriate. Attribute/property combinations in unusual source trees still need verification. Keep the old data until the application has been tested. If the source is itself a subvolume or an fstab mount, adapt the migration rather than copying this directory example unchanged.

`chattr -C DIRECTORY` changes the inheritance policy for future files. It does not rewrite existing file data back into checksummed compressed extents.

## Compression

Compression can reduce storage and I/O at a CPU cost. Benefits depend on the data. Already-compressed video, archives, and encrypted files often gain little.

### Mount policy and per-inode policy

```fstab
UUID=REPLACE_WITH_BTRFS_UUID /mnt/data btrfs noatime,compress=zstd:3 0 0
```

Most filesystem-specific options, including `compress`, `nodatacow`, and `autodefrag`, are shared across mounts of the same filesystem. A later subvolume mount does not create an independent policy pool. Use inode properties/attributes for targeted policy.

```bash
sudo btrfs property set /path/to/directory compression zstd
sudo btrfs property get /path/to/directory compression

# Disable compression while keeping normal CoW/checksums:
sudo btrfs property set /path/to/directory compression none
lsattr -d /path/to/directory

# Remove that explicit property and return to default policy:
sudo btrfs property set /path/to/directory compression ''
```

`none`/`no` sets the no-compress attribute (`m`); the empty value clears the explicit policy. The installed property interface selects an **algorithm**, not a compression level. The NOCOW/compression combination is incompatible; setting `zstd` on a NOCOW directory was rejected in the fixture.

Changing a property or mount option does not retroactively recompress old extents. It affects subsequent writes and inherited policy for new files as applicable.

### Levels on this baseline

| Setting | Meaning |
|---|---|
| `zstd:3` | Practical starting point; default zstd level when not otherwise specified |
| `zstd:1` | Less compression effort; compare real data/CPU use |
| `zstd:-1` through `zstd:-15` | Supported negative fast levels on the supported kernel baseline |
| Higher positive zstd levels, up to `15` | More effort/memory; savings may diminish |
| `compress-force=zstd:3` | Attempts compression despite normal incompressibility decisions; output may still be stored uncompressed |

The supported zstd range here is **-15 through 15**, with `0` selecting the default. The old note's positive-only range was incomplete. Btrfs's range is not the full range of the standalone `zstd` command.

### Measure stored extents

```bash
sudo compsize -b -x /path/to/data
sudo btrfs filesystem du -s /path/to/data
```

`compsize` requires privileges for the relevant ioctls. Recently written data must have reached allocation/writeback before its report is meaningful; `sync -f /path/to/data` can finish that writeback when needed.

| compsize column | Meaning |
|---|---|
| `Disk Usage` | Stored extent bytes, with the tool's profile/accounting caveats |
| `Uncompressed` | Size of the extents before compression; can include partially used/pinned extent waste |
| `Referenced` | Referenced logical content, accounting differently for sharing/holes |
| `Perc` | Disk usage divided by uncompressed extent size |

`Uncompressed - Disk Usage` estimates compression savings for the scanned extents. It is not “free space recovered as a percentage of the whole drive.” Metadata, replication profiles, snapshots outside the scan, and other files remain separate concerns. Use `-b` for arithmetic rather than parsing rounded `G`/`M` strings.

### Recompressing old files

```bash
# Explicit rewrite operation: can break extent sharing and increase used space.
sudo btrfs filesystem defragment -r -czstd -L 3 /path/to/data
```

The installed `-L`/`--level` option is supported; positive and negative level examples were exercised on the fixture. Defragmentation is not routine SSD maintenance. It can improve an actual fragmented workload, but rewrites shared extents and can substantially increase snapshot/reflink space consumption. Compression properties and the defrag invocation's one-time compression request are different controls.

### Direct I/O on Linux 7.3

The installed manual documents the newer handling of checksummed direct writes. On the running 7.3 release candidate, an aligned `dd ... oflag=direct` zero-filled fixture produced uncompressed extents while a buffered write of the same size compressed. A direct-I/O benchmark therefore bypasses transparent compression for those writes. Benchmark the application's real write mode; do not generalize old 6.x direct-write fallback advice to this baseline.

## Modern fstab

```fstab
UUID=REPLACE_WITH_BTRFS_UUID /mnt/data btrfs noatime,compress=zstd:3,nofail,x-systemd.automount 0 0
```

Add `subvol=/@data` when deliberately mounting that existing subvolume. Omit it when the default subvolume is intended. Add optional UI/timeouts only when their behavior is wanted; [[Mounting and fstab]] explains them.

| Option | Current interpretation |
|---|---|
| `ssd` | Usually auto-detected; do not force a hardware assumption across all devices |
| `space_cache=v2` | Redundant for a filesystem already using free-space-tree; not a harmful obsolete flag requiring urgent removal |
| `discard=async` | Automatic on supported devices; can be explicit for clarity |
| `noatime` | Avoids atime updates; changes access-time semantics |
| `autodefrag` | Automatic small-write defragmentation; workload dependent and can break sharing |
| `nodatacow` | Broad policy affecting newly created files; normally prefer targeted `+C` |

“Autodefrag has zero physical benefit on SSDs” was too absolute: fragmentation can affect metadata/I/O behavior without mechanical seeks. The relevant questions are measured benefit, extra writes, and sharing loss. Likewise, no fixed `commit=` value is a universal performance upgrade.

After editing:

```bash
sudo findmnt --verify --verbose
sudo systemctl daemon-reload
```

Then deliberately test the affected mount. A reload does not remount a live filesystem or independently apply different compression policies to each subvolume.

## Subvolume Operations

```bash
sudo btrfs subvolume list -p /
sudo btrfs subvolume show /home
btrfs subvolume get-default /

# Creation and snapshots: use new destination paths.
sudo btrfs subvolume create /mnt/data/work
sudo btrfs subvolume snapshot /mnt/data/work /mnt/data/work-copy
sudo btrfs subvolume snapshot -r /mnt/data/work /mnt/data/work-ro
```

A writable snapshot is a writable clone, not an automatic rollback of the source. A read-only snapshot has the subvolume `ro` property, distinct from mounting an otherwise writable subvolume with `-o ro`.

Useful list flags:

| Flag | Meaning |
|---|---|
| `-p` | Parent ID |
| `-u` / `-q` / `-R` | Subvolume UUID / parent UUID / received UUID |
| `-s` / `-r` | Only snapshots / only read-only subvolumes |
| `-a` | Show all subvolumes with paths distinguished relative to the supplied mount |
| `-d` | Deleted subvolumes whose cleanup is not yet complete |

The old note incorrectly described `-a` as listing pending deletions; that is `-d`.

### Top-level versus mounted tree

The top-level subvolume has ID **5**. In a flat layout, `@`, `@home`, and snapshot stores are siblings under it. They can be mounted at different paths in the visible system tree.

```bash
src='/dev/disk/by-uuid/REPLACE_WITH_BTRFS_UUID'
sudo install -d -m 0755 /mnt/btrfs-top
sudo mount -t btrfs -o subvolid=5 -- "$src" /mnt/btrfs-top
sudo btrfs subvolume list -p /mnt/btrfs-top
# When finished inspecting:
sudo umount /mnt/btrfs-top
```

This does not make another copy of the filesystem. It reveals the top-level tree. Do not confuse a directory under a mounted `@` with a top-level sibling subvolume.

### Snapshots are not recursive

A nested subvolume is a snapshot boundary. A snapshot of its parent contains a placeholder rather than the nested subvolume's contents. This was verified with a nested fixture file that was absent from the parent snapshot.

Flat layouts simplify finding and replacing system subvolumes, but nested layouts are valid too. They are not inherently corrupt or impossible to manage. Separately mounted nested subvolumes need deliberate mount/rollback handling, and deleting a parent requires handling descendants.

A filesystem snapshot preserves the filesystem's view at a point in time. It does not automatically flush an application's userspace buffers or coordinate a database transaction across several filesystems/subvolumes. Quiesce the application or use its backup integration when that consistency is required.

### Deletion and default-subvolume changes

```bash
# Deletes this subvolume/snapshot tree; choose the target deliberately.
sudo btrfs subvolume delete /mnt/data/work-copy

# Wait for asynchronous deletion cleanup where required:
sudo btrfs subvolume sync /mnt/data
```

Deleted space may be released asynchronously, and shared extents remain allocated while other references exist.

`btrfs subvolume set-default ID MOUNT` changes what mounts without an explicit subvolume selection use. It does not override a boot entry or fstab that specifies `subvol=/@`. Do not flip received/read-only snapshots writable casually: incremental send/receive relies on snapshot identity and unchanged parent contents.

### Rollback is a coordinated procedure

A root rollback must consider the selected root subvolume, fstab, boot command line, initramfs/kernel files on the ESP, and any separately mounted application state. Root and home do not become one atomic unit merely because a tool takes two snapshots close together.

The local snapshot manager outside `user_scripts/drives/` was not revalidated in this review. Do not infer its current retention, timer schedule, or rollback guarantees from the old notes. See [[Disk Verification]] for observed timers and subvolumes.

## Send / Receive (incremental backups)

Create/keep read-only source snapshots. The receiver must have the matching parent for an incremental stream. An initial full transfer establishes it.

```bash
# Bash example. pipefail makes a failed sender visible even if receive exits 0.
set -o pipefail

sudo btrfs send /mnt/source-snaps/root-001 | \
    sudo btrfs receive /mnt/backup

# After creating the next read-only source snapshot:
sudo btrfs send -p /mnt/source-snaps/root-001 /mnt/source-snaps/root-002 | \
    sudo btrfs receive /mnt/backup
```

These paths represent existing snapshots and a destination on a mounted Btrfs backup filesystem. `receive` creates the sent subvolume names beneath the destination. Keep the source parent and its received counterpart unchanged for the next incremental transfer.

The full and incremental sequence was exercised on disposable subvolumes, including verification of a new file in the received increment. Sending to a directory on the same test filesystem tested the mechanics; a real backup should have independent failure coverage and a tested restore.

Check exit statuses, received contents, and the destination's health before deleting source snapshots. A successful pipe is not a substitute for a restore test. Off-machine transport can carry the stream, but transport authentication and remote administration are outside this note's local test.

## Integrity & Space

### Understand what each command checks

| Tool | Purpose | Important limit |
|---|---|---|
| `btrfs device stats` | Persistent filesystem/device error counters | Historical counters; inspect changes and logs |
| `btrfs scrub` | Read allocated data/metadata and validate available checksums | Does not validate NOCOW file data; repairs need a good replica |
| `btrfs check --readonly` | Offline structural consistency checking | Different from scrub; unmount all relevant mounts first |
| `btrfs filesystem usage` | Allocation, profiles, used/unallocated/slack space | Not a file-by-file listing |
| `btrfs filesystem du` | Extent sharing/exclusive-use accounting for selected paths | Not identical to compression or whole-pool accounting |
| `compsize` | Compression and referenced extent accounting | Selected scan and tool caveats apply |

```bash
sudo btrfs device stats /
sudo btrfs scrub status /
sudo btrfs balance status /
sudo btrfs filesystem usage /
```

### Scrub

```bash
# Actual scrub; normal mode may repair from valid replicas.
sudo btrfs scrub start -B /
sudo btrfs scrub status /
```

`-B` stays in the foreground. `-r` suppresses repair, but a read-only scrub of a read-write-mounted filesystem can still cause internal filesystem writes. To avoid writes from scrub, both the scrub and the filesystem must be read-only. This distinction matters for recovery images.

On a single-device filesystem with `Data,single`, an unreadable/corrupt data extent has no second data copy for scrub to repair from. `Metadata,DUP` supplies duplicate metadata on the same device, not protection against loss of that device.

The installed manual notes that scrub can be interrupted by suspend/freezing/signals on current kernels. Inspect status and use `btrfs scrub resume` when appropriate. A monthly schedule is a reasonable maintenance policy, but inspect packaged/local timer units before enabling a guessed service name. This review scrubbed only the fixture, not the live root filesystem.

### Offline check is not a routine repair command

```bash
# Filesystem must be unmounted; use the correct underlying Btrfs device.
sudo btrfs check --readonly /dev/disk/by-uuid/REPLACE_WITH_BTRFS_UUID
```

Do not run a structural check on a live mounted root and treat its output as an offline result. `btrfs check --repair`, `btrfs rescue ...`, and `btrfs restore` are different tools with different recovery purposes. Select them from the actual failure evidence and use an image/clone when appropriate; do not try a series of mutations merely because mount failed.

### Allocated is not used

Btrfs allocates device space into block groups for data, metadata, and system structures. A group can be allocated while partly empty. Thus:

```text
device capacity = allocated space + device unallocated space
allocated group space = used contents + free space inside those groups
```

Profiles multiply physical space requirements. A 3 GiB logical metadata allocation under DUP uses about 6 GiB of device space. Device slack is space outside Btrfs's current device size but inside the underlying block device; it is another distinct quantity.

A near-zero `Device unallocated` value alone does **not** prove ENOSPC. This host had roughly 1 MiB unallocated while its allocated metadata groups were only about 53% used and estimated filesystem free space remained around 18 GiB. Interpret the complete report.

## ENOSPC: When df Says Free But Writes Fail

ENOSPC can reflect insufficient usable data/metadata space, profile constraints, temporary relocation needs, or quotas. EDQUOT is a quota limit, not necessarily a physically full device. A forced read-only filesystem after errors is another condition; read the logs rather than treating every failed write as ENOSPC.

```bash
sudo btrfs filesystem usage /
sudo btrfs balance status /
sudo btrfs device stats /
journalctl -k -b -g 'BTRFS|ENOSPC|No space|quota|read-only' --no-pager
```

If quotas are enabled, inspect their limits as part of the diagnosis. Snapshot references can keep deleted file extents alive. Removing one pathname may free almost nothing if snapshots still reference the data.

### A measured escalation sequence

1. Stop avoidable writers. Establish whether the problem is capacity, metadata pressure, a quota, or an I/O/corruption condition.
2. Delete selected expendable data or old snapshots using the relevant manager. Inspect its list before selecting IDs; root and home snapshot IDs are not interchangeable.
3. Wait for subvolume deletion cleanup if needed: `sudo btrfs subvolume sync /`.
4. Reclaim completely empty block groups first:

```bash
sudo btrfs balance start -dusage=0 -musage=0 /
sudo btrfs filesystem usage /
```

5. If the evidence supports packing sparsely occupied data groups and enough working space exists, try a bounded pass and inspect the result:

```bash
sudo btrfs balance start -dusage=5,limit=2 /
sudo btrfs filesystem usage /
```

A usage-filtered balance relocates selected groups. It does not invent capacity or remove snapshot references. Broader metadata/data relocation can itself fail for lack of workspace. Do not automatically run repeated 5/10/20/30/50% passes or a full balance because “unallocated” is small. Do not suppress errors as harmless without reading them.

### Temporary extra device: advanced recovery, not routine maintenance

Adding a spare block device can provide allocator space, but immediately makes that device part of the filesystem's durable storage. Removing it later must successfully relocate everything back under the existing profiles.

A loop-backed rescue image must live on a **different, stable filesystem**, with real backing capacity, and must remain available until Btrfs removal completes. Putting it on the filesystem being rescued creates a circular dependency. Another subvolume/mount of that same filesystem is not a different filesystem.

The critical lifecycle is:

```text
provision independent backing storage
→ create/record the loop mapping if used
→ btrfs device add
→ perform narrowly chosen recovery/free space
→ btrfs device remove, and verify success
→ only then detach loop and remove the image
```

If removal fails, preserve the device/image and its mapping information. Do not delete/detach it or reboot into a setup that cannot reassemble the now-multi-device filesystem. A temporary add/remove cycle was not tested under genuine metadata exhaustion here. The local rescue script is described, with its concrete limitations, in [[Local Drive Tools]].

## Resizing

### Order and invariants

**Grow the partition first, update/verify the kernel's partition size, then grow Btrfs. Shrink Btrfs first, verify it fits, then shrink the partition.** Never change the partition start in a resize-only operation.

Free bytes in `df` do not determine a valid shrink size. Relocation, profiles, current extents, and kernel restrictions matter. `btrfs inspect-internal min-dev-size` is an estimate, not a guarantee that any larger arbitrary resize will succeed.

Growing a Btrfs filesystem to the size of its already-expanded underlying device:

```bash
sudo btrfs filesystem resize max /mnt/data
```

On multi-device filesystems, explicitly select the correct Btrfs device ID where required; `max` is not “grow all devices.” A filesystem resize does not move a following partition or create contiguous space outside the partition.

### Use the local helper only within its scope

```bash
python3 "$HOME/user_scripts/drives/format/resize_btrfs.py" --mountpoint / --status

# Mutating alternatives, after reviewing status and backups:
python3 "$HOME/user_scripts/drives/format/resize_btrfs.py" --mountpoint /mnt/data --shrink 1GiB
python3 "$HOME/user_scripts/drives/format/resize_btrfs.py" --mountpoint /mnt/data --grow 1GiB
```

Its `--shrink`/`--grow` sizes are **deltas**, not final sizes. `GiB` is binary; `GB` is decimal. It elevates itself and requires the intended Python 3.15 environment.

The reviewed implementation supports a mounted, read-write, single-device Btrfs filesystem **directly on a GPT partition**. It rejects LUKS/LVM/RAID mapping layers, preserves the partition start, writes a table backup, checks overlaps, updates the selected kernel partition with `partx`, and verifies size/start before filesystem expansion. The old claim about a fixed “500M safety margin” does not describe this implementation.

Both the underlying manual sequence and the actual helper were exercised on a disposable loop-backed GPT image. The helper shrank 768 MiB to 704 MiB and grew it back. This tests the current code path and tool interfaces, not power-loss recovery or every physical layout. A resize is a sequence of persistent changes, not one atomic transaction. After an interruption, inspect the filesystem, on-disk GPT, and kernel bounds before retrying.

For encrypted or layered storage, use a procedure that includes those layers; do not bypass the helper's scope checks.

## Swapfile on Btrfs

A Btrfs swapfile needs a supported extent layout: no holes, NOCOW/uncompressed data, and the documented single-device/single-data-profile constraints. Use a dedicated subvolume outside the snapshots you take. An active swapfile prevents snapshotting its owning subvolume; a separate subvolume also keeps it out of its parent's snapshot data.

```bash
# Use a new path on an existing dedicated swap subvolume.
sudo btrfs filesystem mkswapfile --size 8g /swap/swapfile
sudo swapon /swap/swapfile
swapon --show
```

The helper creates and formats the file with the required policy. Do not run it over an existing active swapfile. The fixture tested creation and `map-swapfile`, not system swap activation.

```fstab
/swap/swapfile none swap defaults 0 0
```

For deactivation before an intended change:

```bash
sudo swapoff /swap/swapfile
```

Ensure memory/other swap can absorb the pages before doing that on a live system.

### Hibernation

```bash
sudo btrfs inspect-internal map-swapfile -r /swap/swapfile
```

This reports the Btrfs-specific resume offset and validates relevant file layout requirements. Do not use `filefrag`'s physical-offset column as a Btrfs resume offset. The boot/initramfs must also locate the correct backing device and resume configuration. Recreating or relocating the swapfile can change the offset.

Zram is volatile RAM-backed swap and cannot by itself preserve a hibernation image across power loss. Hibernation support was not configured or tested in this review.

## NTFS Volumes

NTFS is a different filesystem, including when it lives alongside Btrfs or inside BitLocker. This running kernel's native read/write driver is named `ntfs`; the previous `ntfs3` advice is stale for this system. Use [[Mounting and fstab#Native NTFS on the supported kernel]] for verified options, [[Bitlocker]] for the encrypted outer layer, and [[Fixing Un Mountable NTFS drive]] for errors.

## References and verification limits

Installed btrfs-progs 7.3 manuals were used for options and semantics, including `btrfs(5)`, `btrfs-subvolume(8)`, `btrfs-filesystem(8)`, `btrfs-property(8)`, `btrfs-balance(8)`, `btrfs-scrub(8)`, `btrfs-send(8)`, and `btrfs-receive(8)`. The installed `chattr(1)`, GNU coreutils, and `compsize(8)` references cover their respective interfaces. [[Disk Verification]] records tested behavior and deliberately untested recovery/boot operations.
