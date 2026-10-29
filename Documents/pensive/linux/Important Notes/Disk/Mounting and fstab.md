# Mounting and fstab

> [!abstract] Purpose
> Understand manual mounting, ownership, systemd automounts, and encryption dependencies. Related: [[Storage Stack]] · [[Disk]] · [[BTRFS]] · [[Bitlocker]] · [[Disk Verification]]. Examples target the supported Arch/Linux 7.3+ platform.

## Start with identity and filesystem type

```bash
lsblk -o NAME,PATH,TYPE,SIZE,FSTYPE,FSVER,LABEL,UUID,MOUNTPOINTS
findmnt -o TARGET,SOURCE,FSTYPE,OPTIONS
```

Use a path under `/dev/disk/by-uuid/` or `/dev/disk/by-id/` after matching the actual device. In a shell command, `UUID=...` is accepted by `mount`; `cryptsetup open` normally needs a device path such as `/dev/disk/by-uuid/...`.

Code examples containing `REPLACE_WITH_...` require substitution. fstab examples are **file content**, not shell commands. The shell does not expand `$HOME`, `$(id -u)`, or variables in `/etc/fstab`.

## Manual mount and unmount

For an already-created ext4 filesystem:

```bash
src='/dev/disk/by-uuid/REPLACE_WITH_FILESYSTEM_UUID'
mnt='/mnt/data'
sudo install -d -m 0755 -- "$mnt"
sudo mount -t ext4 -- "$src" "$mnt"
findmnt --mountpoint "$mnt" -o TARGET,SOURCE,FSTYPE,OPTIONS

# After finishing all use of the volume:
sudo umount -- "$mnt"
```

`install -d` creates the directory; `mount` attaches the filesystem there. `umount` is spelled without an `n` after `u`.

For a file or directory whose backing filesystem is unclear:

```bash
findmnt --target "$HOME/Documents" -o TARGET,SOURCE,FSTYPE,OPTIONS
```

For an exact mount-point check, use `findmnt --mountpoint`, not `--target`.

### Read-only mounting

`mount -o ro` prevents ordinary filesystem writes. It is not a universal forensic “no underlying writes” guarantee: some journaled filesystems may replay recovery data during a read-only mount. A read-only underlying mapping/device provides a stronger boundary.

For ext4 diagnosis where journal replay must be avoided, `ro,noload` is the filesystem-specific choice. For Btrfs it is `ro,nologreplay`. These suppress recovery and can expose an older/inconsistent view; copy/recovery procedures must account for that.

### If unmount says “busy”

Leave the filesystem in every terminal (`cd /` is enough for a shell whose current directory is inside it). Close applications using it. Inspect holders:

```bash
sudo fuser -vm /mnt/data
sudo lsof -nP +f -- /mnt/data
```

These tools are useful but can miss cases involving namespaces or Btrfs mapped files. Consult the mount tree and process mappings if the result is incomplete.

`umount -l` detaches a mount from the namespace while references can remain alive. It is not evidence that a USB drive is safe to unplug. A normal successful unmount is the routine removal workflow.

## The six fstab fields

```fstab
# source                target     type   options             dump pass
UUID=REPLACE_WITH_UUID   /mnt/data  ext4   defaults,noatime    0    2
```

| Field | Meaning |
|---|---|
| Source | Filesystem UUID, partition UUID, label, device path, or another supported source |
| Target | Absolute mount directory; `none` for a swap entry |
| Type | Actual driver/filesystem type: `btrfs`, `ext4`, `ntfs`, `exfat`, `vfat`, etc. |
| Options | Comma-separated mount and userspace/systemd options |
| Dump | Usually `0`; legacy `dump` backup selection |
| Pass | Boot-time fsck ordering: conventionally root `1`, other checked filesystems `2`, disabled `0` |

For Btrfs use `0 0`; its boot-time fsck helper is not a substitute for scrub or an offline structural check. NTFS examples also use `0 0` here; the installed NTFS utilities do not provide a complete Windows-equivalent repair pass.

Escape spaces as `\040` and tabs as `\011` in fstab fields, including within a quoted string. Shell-style quotation alone does not make an embedded space a single field.

## What the common options actually do

| Option | Effect | Appropriate use |
|---|---|---|
| `defaults` | Conventional defaults; actual effective flags also depend on filesystem/kernel | Simple baseline, not a performance profile |
| `noatime` | Suppresses access-time updates | When applications do not require atime |
| `relatime` | Limits atime updates while retaining more semantics | General default if atime matters |
| `lazytime` | Defers timestamp persistence under documented conditions | Optional ext4 timestamp-write reduction; not equivalent to a journal commit interval |
| `ro` / `rw` | Read-only / read-write filesystem mount | Select intentionally |
| `noauto` | Not mounted by ordinary automatic fstab processing | Manual mounts; see automount exception below |
| `nofail` | systemd does not require/wait for this mount before completing the local filesystem target | Optional drives; access can still fail or wait |
| `x-systemd.automount` | Creates a kernel autofs trigger; first access starts the real mount | On-demand non-root mounts |
| `x-systemd.idle-timeout=10min` | Attempts idle unmount for an automount | Optional; busy mounts stay mounted |
| `x-systemd.device-timeout=10s` | Limits waiting for the backing device | Choose based on actual device/unlock timing |
| `x-systemd.mount-timeout=30s` | Limits the mount operation | Distinct from waiting for the device |
| `x-gvfs-show` | Desktop file-manager visibility hint | Optional; desktop integration must be checked in the actual session |

With `x-systemd.automount`, `auto` and `noauto` do not suppress the generated automount. `nofail` also does not make a failed access succeed. Browsing a directory in a file manager can trigger the mount.

An automount initially appears as `autofs`. That does not mean the intended data filesystem has already mounted. Inspect both the `.automount` trigger and `.mount` unit when diagnosing it.

## Filesystem-specific data-volume examples

Replace UUIDs and mount paths. Add `nofail` only for a volume the system can actually operate without. Root and essential system paths need a separate boot design.

### Btrfs

```fstab
UUID=REPLACE_WITH_BTRFS_UUID /mnt/data btrfs noatime,compress=zstd:3,nofail,x-systemd.automount 0 0
```

If mounting a specific existing subvolume, add `subvol=/@data`. Without it, Btrfs mounts the default subvolume. Compression settings are filesystem-wide mount policy, not independent per-subvolume settings. See [[BTRFS]].

### ext4

```fstab
UUID=REPLACE_WITH_EXT4_UUID /mnt/data ext4 defaults,noatime,nofail,x-systemd.automount 0 2
```

Use ordinary Unix ownership/permissions. `uid=` and `gid=` are not general ext4 mount options. To make the mounted filesystem's root belong to the current account:

```bash
# Run only after verifying the intended filesystem is mounted here.
sudo chown -- "$(id -u):$(id -g)" /mnt/data
```

This changes one directory's ownership, not every file recursively. For shared data, choose group ownership and permissions according to the intended users.

### Native NTFS on the supported kernel

This system's kernel provides the modern read/write **`ntfs`** module. The old notes' `ntfs3` examples do not match this running kernel. For a manual mount that must select the kernel driver explicitly:

```bash
src='/dev/disk/by-uuid/REPLACE_WITH_NTFS_UUID'
sudo mount -i -t ntfs \
    -o "uid=$(id -u),gid=$(id -g),dmask=0022,fmask=0133,windows_names" \
    -- "$src" /mnt/data
```

`-i` tells util-linux not to invoke a `mount.<type>` helper. This matters if a `mount.ntfs` helper from a FUSE implementation is installed. With fstab, type `ntfs` describes the intended type, but helper availability can affect ordinary `mount` dispatch. Verify `findmnt` reports `ntfs` rather than `fuseblk`; use `mount -i /mnt/data` when explicitly selecting the native path. systemd mount behavior should also be verified if helpers are later added.

```fstab
# Substitute the account's actual numeric IDs; fstab performs no shell expansion.
UUID=REPLACE_WITH_NTFS_UUID /mnt/windows ntfs uid=REPLACE_UID,gid=REPLACE_GID,dmask=0022,fmask=0133,windows_names,noatime,nofail,x-systemd.automount 0 0
```

Check IDs with `id -u` and `id -g` as the intended owner. Masks remove bits: `dmask=0022` yields directories `0755`; `fmask=0133` yields files `0644` in the tested configuration. A single `umask=0022` can make regular files appear executable.

`windows_names` was accepted and rejected Windows-invalid names in temporary-image tests here. Do not infer that every option supported by another NTFS implementation is accepted by this one.

### exFAT and FAT32

```fstab
UUID=REPLACE_WITH_EXFAT_UUID /mnt/usb exfat uid=REPLACE_UID,gid=REPLACE_GID,dmask=0022,fmask=0133,noatime,nofail,x-systemd.automount 0 0
```

Both kernel support and userspace tools must be present for the operations that need them: `exfatprogs` creates/checks exFAT; the kernel driver mounts it. FAT uses `vfat` and tools from `dosfstools`.

These filesystems do not provide ordinary Linux per-file Unix ownership in the same way as ext4/Btrfs. The mount supplies a Linux ownership/mode view. Mounting ext4 with exFAT-style ownership options is not a solution to permissions.

## Apply an fstab change deliberately

```bash
sudo findmnt --verify --verbose
sudo systemctl daemon-reload
```

Verification checks fstab's structure and available targets/sources. It does **not** exercise every filesystem driver's mount-option parser or guarantee future hotplug/unlock success. A mount test and kernel log remain necessary.

For an unmounted ordinary entry:

```bash
sudo mount /mnt/data
findmnt --mountpoint /mnt/data -o TARGET,SOURCE,FSTYPE,OPTIONS
```

For a newly configured automount:

```bash
sudo systemctl start "$(systemd-escape --path --suffix=automount /mnt/data)"
# Accessing this path triggers the real mount and can wait for the device.
ls /mnt/data
findmnt --mountpoint /mnt/data -o TARGET,SOURCE,FSTYPE,OPTIONS
```

Inspect failure details using the mount unit's escaped name:

```bash
systemctl status "$(systemd-escape --path --suffix=mount /mnt/data)" --no-pager
journalctl -b -u "$(systemd-escape --path --suffix=mount /mnt/data)" --no-pager
journalctl -k -b --no-pager
```

`daemon-reload` refreshes generated units; it does not remount an already-mounted filesystem with new flags. Plan a remount or normal unmount/mount according to the filesystem and its users. `mount -a` operates on all eligible fstab entries, so it is broader than testing the entry just edited.

## Encryption and mount dependencies

Opening a container and mounting its inner filesystem are separate operations.

```text
outer encrypted UUID → cryptsetup mapping → inner filesystem UUID → mount point
```

A boot-unlocked LUKS example:

```text
# /etc/crypttab
media UUID=REPLACE_WITH_OUTER_LUKS_UUID none luks
```

```fstab
# /etc/fstab
/dev/mapper/media /mnt/media ext4 defaults,noatime 0 2
```

The crypttab entry creates the mapping named `media`; fstab mounts that mapper. `none` allows systemd's normal key discovery/password mechanisms; it does not mean that no unlocking credential is needed. An optional disk can use an appropriately designed `nofail`/timeout policy on both layers. An automount cannot manufacture a key or unlock an unconfigured encrypted device.

For BitLocker, use the documented `bitlk` crypttab option and a supported credential method; see [[Bitlocker]]. For root encryption, changes may also need to reach the initramfs/boot configuration. A data-volume example is not a root-encryption recipe.

## Desktop mounting and safe removal

UDisks uses the active session and Polkit:

```bash
udisksctl mount -b /dev/disk/by-uuid/REPLACE_WITH_FILESYSTEM_UUID
udisksctl unmount -b /dev/disk/by-uuid/REPLACE_WITH_FILESYSTEM_UUID
```

For encryption: unlock the outer device, use the **reported** mapper for mount/unmount, then lock the outer device. `udisksctl unlock --read-only` is supported here.

For a USB disk, after every filesystem is unmounted and every mapping is closed:

```bash
udisksctl power-off -b /dev/disk/by-id/REPLACE_WITH_WHOLE_USB_DISK_ID
```

Power-off acts on the physical drive and can affect sibling volumes on it. It is not a routine command for the internal root drive. `sync` alone neither unmounts a filesystem nor prevents applications from writing again.

## References and verification

Installed manuals checked: `mount(8)`, `umount(8)`, `findmnt(8)`, `fstab(5)`, `systemd.mount(5)`, `crypttab(5)`, `udisksctl(1)`. Native NTFS and exFAT mount/write/unmount examples were exercised on disposable image-backed devices; real desktop hotplug, boot prompts, and file-manager visibility were not changed or tested. See [[Disk Verification]].
