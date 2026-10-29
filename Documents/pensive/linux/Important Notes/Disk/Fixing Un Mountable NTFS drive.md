# Diagnosing and Recovering an NTFS Volume That Will Not Mount

> [!abstract] Start with the failing layer
> An unlock that succeeded followed by a mount failure narrows the investigation to the decrypted device, filesystem/driver/options, and hardware path. It does **not** prove BitLocker or every decrypted block is healthy. This procedure applies to plain NTFS too: skip the encryption steps when there is no container.
>
> Related: [[Bitlocker]] · [[Storage Stack]] · [[Mounting and fstab]] · [[Disk]] · [[Disk Verification]].

## Classify the error before changing the volume

| Evidence | What it establishes | Useful next step |
|---|---|---|
| Generic “wrong fs type, bad option, bad superblock” | Mount failed; reason still unknown | Read kernel log and actual requested type/options |
| “Unknown filesystem type” or absent driver | Driver selection/support problem is possible | Check module and native mount type |
| Dirty-volume refusal | Volume requests consistency handling | Prefer read-only access; inspect Windows state and logs |
| Hibernated/Fast Startup refusal | Windows state cannot safely be shared writable | Resume Windows and fully shut down; do not bypass it |
| `$MFTMirr` differs from `$MFT` | NTFS metadata inconsistency detected | Preserve data; decide whether hardware needs imaging first |
| Read I/O errors, USB resets, disappearing drive | Lower-layer failure is possible | Stabilize connection and image before repair |
| Mapper exists but filesystem probe fails | Mapping is present; contents not established | Verify target/container state and logs |

MFT/mirror errors can result from corruption, interrupted writes, or hardware-path failures. A generic error does not uniquely identify a cause. The SoftRAID/FakeRAID suggestion in some old error text is not evidence that the device actually uses RAID.

## Capture identity and logs

```bash
lsblk -o NAME,PATH,TYPE,SIZE,FSTYPE,FSVER,LABEL,UUID,MOUNTPOINTS
findmnt -o TARGET,SOURCE,FSTYPE,OPTIONS
journalctl -k -b -g 'ntfs|NTFS|I/O error|Buffer I/O|reset|usb|USB|nvme' --no-pager
```

Use the current device tree and persistent identifiers. `/dev/dm-0` is a transient number, not an enduring identity. Record the exact error and command used before trying a different command.

For BitLocker, the outer partition/device contains encrypted data; the mapper contains the NTFS view. `ntfsfix` and `chkdsk` must operate on the NTFS volume, not the locked outer sectors.

## Use the actual supported driver

On this running kernel, the native read/write NTFS module is **`ntfs`**. The old notes' `ntfs3` command does not match this build.

```bash
modinfo ntfs
# A built-in/already-loaded driver can appear here:
cat /proc/filesystems
```

Absence from `/proc/filesystems` does not prove a module is unavailable; it may simply not be loaded. `modinfo` checks the installed module, and the actual mount is the meaningful test. If `/proc/config.gz` is available:

```bash
zgrep -E 'CONFIG_NTFS|CONFIG_FUSE' /proc/config.gz
```

This system has `CONFIG_NTFS_FS=m` and lacks `CONFIG_NTFS3_FS`. A disposable NTFS image was successfully mounted/written/remounted with `mount -i -t ntfs`. `-i` bypasses external mount helpers; ordinary `mount -t ntfs` can select a `mount.ntfs` helper if one is later installed.

Changing a driver can distinguish a driver/options problem. It does not itself repair NTFS metadata.

## Read-only triage

### Encrypted volume

```bash
outer='/dev/disk/by-uuid/REPLACE_WITH_OUTER_BITLOCKER_UUID'
sudo cryptsetup open --type bitlk --readonly "$outer" ntfs_recovery
vol='/dev/mapper/ntfs_recovery'
sudo cryptsetup status ntfs_recovery
sudo blkid "$vol"
```

### Plain NTFS volume

Use this instead of the encrypted setup:

```bash
vol='/dev/disk/by-uuid/REPLACE_WITH_NTFS_UUID'
sudo blkid "$vol"
```

### Attempt a read-only mount

```bash
sudo install -d -m 0755 /mnt/ntfs-recovery
sudo mount -i -t ntfs -o ro -- "$vol" /mnt/ntfs-recovery
findmnt --mountpoint /mnt/ntfs-recovery -o TARGET,SOURCE,FSTYPE,OPTIONS
```

If it mounts and the hardware is stable, copy important data to independent healthy storage. If reads cause repeated hardware errors, stop exploratory filesystem walks and consider an image-first strategy. A failed read-only mount does not justify a forced writable mount.

After copying:

```bash
sudo umount /mnt/ntfs-recovery
# Only if this workflow opened the encrypted mapping:
sudo cryptsetup close ntfs_recovery
```

A read-only encrypted mapping blocks underlying writes through that mapping. A mount option alone is a filesystem-level request and is not a universal no-write forensic guarantee across all drivers/filesystems.

## ntfsfix: what it does and does not do

On this package set:

```bash
pacman -Qo /usr/bin/ntfsfix
ntfsfix --help
```

`ntfsfix` is provided by **`ntfsprogs`**, not by the separate FUSE driver package. It addresses a limited set of fundamental inconsistencies, handles some NTFS state, and normally requests a Windows check. It is not a complete `chkdsk` replacement.

Run it only against the unmounted NTFS volume:

```bash
sudo ntfsfix -n "$vol"
```

`-n` is the no-action mode. The fixture's entire image hash was unchanged after this command. A successful clean-image result validates the interface, not every corruption case.

| Option | Documented effect | What it does not establish |
|---|---|---|
| `-n`, `--no-action` | Does not write; shows the attempted checks/actions | Full NTFS consistency or hardware health |
| `-d`, `--clear-dirty` | Clears the dirty flag if the volume can be fixed/mounted | That Windows hibernation is resolved or the volume fully repaired |
| `-b`, `--clear-bad-sectors` | Clears the recorded bad-sector list; useful after cloning to good media | That the original medium has become healthy |

A deliberate write-capable basic triage attempt on stable media is `sudo ntfsfix "$vol"`. It must use a writable mapping, with the filesystem unmounted, and changes the volume. Prefer preserving data first and choose the operation from actual evidence.

Do **not** turn `ntfsfix -b -d` into a generic “make the drive healthy” recipe. Clearing remembered bad sectors on the same failing medium is particularly misleading. Clearing the dirty flag is not a substitute for completing filesystem recovery.

On this installation, `fsck.ntfs` is absent. Other distributions/package versions can provide different helpers. Never assume a Linux command named `fsck.ntfs` is a complete repair tool or simply a symlink to `ntfsfix` without inspecting the actual installed implementation.

The local `ntfs_fix.sh` currently invokes `ntfsfix -b -d` and prints an unjustified hibernation-cleared message. It was inspected, not executed against a real drive, and is **not recommended as the default workflow**. See [[Local Drive Tools]].

## Windows repair, even if Arch is the everyday OS

Arch remains the operating environment for these notes. Windows tools are included because NTFS/BitLocker are Windows formats and some repair operations are best performed with their supported filesystem tooling. If Windows is unavailable, preserve/copy/image data instead of pretending Linux has an identical repair command.

On Windows, identify the actual volume letter after unlocking BitLocker. In an Administrator Command Prompt:

```bat
chkdsk X: /f
```

`/f` fixes filesystem errors and needs a lock on the volume; Windows may schedule work at restart if it cannot acquire the lock. `/r` includes `/f` and adds bad-sector/readability work, making it more intensive. Neither should precede imaging when the medium is failing. These meanings were checked against [Microsoft's chkdsk documentation](https://learn.microsoft.com/en-us/windows-server/administration/windows-commands/chkdsk); the commands were not run on Windows here.

For a BitLocker recovery password:

```bat
manage-bde -status
manage-bde -unlock X: -RecoveryPassword REPLACE_WITH_THE_ACTUAL_48_DIGIT_RECOVERY_PASSWORD
```

For an interactive password prompt:

```bat
manage-bde -unlock X: -Password
```

Then run the selected filesystem check. Recovery-password, password, and external `.bek` recovery-key methods are distinct. See [Microsoft's manage-bde unlock reference](https://learn.microsoft.com/en-us/windows-server/administration/windows-commands/manage-bde-unlock).

### Windows installation/recovery media

Drive letters in WinRE can differ from a normal Windows boot. Identify the volume before assigning or using a letter:

```text
diskpart
list volume
select volume REPLACE_WITH_VOLUME_NUMBER
assign letter=Z
exit
```

Unlock `Z:` if encrypted, then use the appropriate check. Replace placeholders; do not copy a fabricated recovery password. A successful repair log matters more than a fixed “reboot twice” rule. Complete any scheduled repair and fully shut down Windows before writable sharing with Arch.

## Hardware errors: image before repair

Repeated media I/O errors, disconnects, or an unstable device justify preserving readable sectors before write-capable filesystem repair. Check cable/power/bridge conditions as well as the medium. Read SMART using the correct transport:

```bash
sudo smartctl --scan-open
sudo smartctl -x /dev/disk/by-id/REPLACE_WITH_WHOLE_DISK_ID
```

Use `-d sat` only when appropriate for the actual bridge. A SMART “PASSED” result does not overrule repeated read failures.

### GNU ddrescue first pass

The Arch package is `ddrescue`. Keep the image **and its mapfile on healthy independent storage** with enough capacity for the source. A sparse file's apparent size still matters for eventual capacity planning.

For an encrypted partition, image the **outer encrypted partition** to preserve BitLocker metadata and encrypted sectors together. If the partition table or disk layout is also in doubt, a whole-disk image may be appropriate instead; identify the scope deliberately.

```bash
src='/dev/disk/by-id/REPLACE_WITH_SOURCE_PARTITION_ID'
rescue_dir='/mnt/healthy-recovery'

sudo ddrescue -n "$src" \
    "$rescue_dir/bitlocker-partition.img" \
    "$rescue_dir/bitlocker-partition.map"
```

`-n` skips scraping in the first pass. The mapfile records what was read and lets the same rescue resume. Retain the same source/image/mapfile relationship; never reuse another rescue's mapfile. `-f` is not needed for an ordinary output image file and is deliberately omitted.

A possible follow-up, when additional reads are justified:

```bash
sector_size=$(sudo blockdev --getss "$src")
sudo ddrescue -d -b "$sector_size" -r1 "$src" \
    "$rescue_dir/bitlocker-partition.img" \
    "$rescue_dir/bitlocker-partition.map"
```

`-d` requests direct input; the sector size must match the source's requirements. Some bridges/devices do not support that path correctly. Re-evaluate errors instead of adding unlimited retries. The direct-input/resume syntax was tested on regular-file fixtures; no failing medium was available to validate recovery yield.

### Open the partition image read-only on Arch

For an image containing the BitLocker partition itself (not a whole disk):

```bash
image='/mnt/healthy-recovery/bitlocker-partition.img'
loop=$(sudo losetup --find --show --read-only -- "$image")
sudo cryptsetup open --type bitlk --readonly "$loop" recovered_bitlk
sudo blkid /dev/mapper/recovered_bitlk
sudo install -d -m 0755 /mnt/recovered-bitlk
sudo mount -i -t ntfs -o ro /dev/mapper/recovered_bitlk /mnt/recovered-bitlk
```

Cleanup only after the corresponding earlier steps succeeded:

```bash
sudo umount /mnt/recovered-bitlk
sudo cryptsetup close recovered_bitlk
sudo losetup --detach "$loop"
```

A whole-disk image instead needs partition discovery/offset handling; this partition-image recipe must not be pointed blindly at it. If a step fails, preserve the image/mapfile and unwind only resources actually created.

Keep a master image unchanged. Perform write-capable repair experiments on a working copy/clone. Missing source sectors remain missing: a successful copy of the readable sectors cannot reconstruct unreadable data by itself.

## Retest after repair

After the chosen repair completes and Windows has fully shut down:

1. Re-identify the device and inspect recent logs.
2. Unlock normally if encrypted.
3. Probe the inner filesystem and test a read-only mount first if condition remains uncertain.
4. For a clean volume, use the normal native NTFS ownership/mask options from [[Mounting and fstab]].
5. Read representative files and check any available application checksums/backups.
6. Unmount, close the mapping, then optionally power off a USB drive.

Mount success is a useful milestone; it is not a checksum of all recovered files. Continue monitoring the hardware path if resets/errors were involved.

## Habits that prevent repeat failures

Unmount before disconnecting. Close encrypted mappings after unmount. Avoid concurrent writable access to a filesystem from two operating systems. Do not share NTFS writable while Windows retains hibernated/Fast Startup state. Keep independent backups and replace demonstrably faulty cables/bridges/media.

Switching to exFAT can fit a simple portable-storage use case, but changes filesystem features and does not make unsafe removal harmless. Both loop and exFAT support are available in this current kernel; old statements that they were absent are stale.

## Verification boundary

Tested: clean NTFS creation, no-action `ntfsfix` with an unchanged image hash, native read/write mount, ownership/masks, `windows_names`, persistence after remount, and read-only write rejection. Tested separately: ddrescue first pass/resume and matching output hash on healthy regular files.

Not tested: real BitLocker unlock, damaged NTFS repair, Windows/WinRE commands, failing-media recovery, or boot-time automation. These are documented procedures with explicit limits, not claims of successful recovery on this hardware.
