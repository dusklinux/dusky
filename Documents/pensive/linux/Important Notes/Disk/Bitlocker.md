# Accessing a BitLocker Drive on Arch Linux

> [!abstract] Scope
> Identify a BitLocker volume, unlock a decrypted mapping, mount its inner filesystem, and close the layers in reverse order. Related: [[Storage Stack]] · [[Mounting and fstab]] · [[Fixing Un Mountable NTFS drive]] · [[Disk Verification]].
>
> cryptsetup/UDisks syntax was checked locally. No BitLocker device was attached for an unlock test. Native NTFS mounting was tested separately on a disposable NTFS image. Do not confuse these two levels of verification.

## Understand the two layers

```text
outer device: BitLocker metadata + encrypted sectors
    │ cryptsetup open --type bitlk
    ▼
/dev/mapper/bitlk_data: decrypted block-device view
    │ mount the filesystem detected inside
    ▼
/mnt/bitlk: files and directories
```

Unlocking does not mount the filesystem. Mounting does not permanently decrypt the disk. While the mapping is open, reads are decrypted and permitted writes are encrypted back into the container.

Normal BitLocker access does not require converting the volume to LUKS. `cryptsetup luksFormat` would create a different encrypted format and destroy access to the existing contents; it is not an unlock operation.

## Identify the outer volume

```bash
lsblk -o NAME,PATH,TYPE,SIZE,FSTYPE,FSVER,LABEL,UUID,MOUNTPOINTS
sudo blkid
```

The outer signature may be reported as `BitLocker`. Choose the device containing that signature—usually a partition, but do not assume every encrypted device must have a partition table.

```bash
outer='/dev/disk/by-uuid/REPLACE_WITH_OUTER_BITLOCKER_UUID'
sudo cryptsetup bitlkDump "$outer"
```

`bitlkDump` inspects BitLocker header information. Do not add volume-key-dump options for routine identification. If the signature or header is not recognized, confirm the target and logs before trying a mount or filesystem repair.

## Unlock with an explicit mapper name

For diagnosis or copying data, start read-only:

```bash
sudo cryptsetup open --type bitlk --readonly "$outer" bitlk_data
sudo cryptsetup status bitlk_data
lsblk -f /dev/mapper/bitlk_data
sudo blkid /dev/mapper/bitlk_data
```

The credential prompt accepts supported BitLocker credentials, such as an appropriate password or numerical recovery password. Windows TPM-based automatic unlock is not a portable Linux unlock recipe; have a supported recovery/password method. Supported protectors/container states must be checked against the installed cryptsetup documentation and actual volume. “Full BitLocker support” is too broad a promise.

Use a mapper name that is not already active. A named mapper is stable for its lifetime; `/dev/dm-N` numbers are transient. The same name can later be reused for another device, so inspect `cryptsetup status` when identity matters.

**A mapper's existence is not proof that every decrypted block is correct.** Confirm the inner filesystem and inspect logs. A successful normal credential-based unlock narrows the diagnosis, but hardware errors, unsupported states, wrong targets, driver selection, and mount options can still cause failures.

## Mount the detected inner filesystem

BitLocker data volumes commonly contain NTFS. Use the type actually detected. For NTFS on the supported running kernel:

```bash
sudo install -d -m 0755 /mnt/bitlk
sudo mount -i -t ntfs -o ro -- /dev/mapper/bitlk_data /mnt/bitlk
findmnt --mountpoint /mnt/bitlk -o TARGET,SOURCE,FSTYPE,OPTIONS
```

The native driver here is `ntfs`, not the old note's `ntfs3`. `mount -i` bypasses external mount helpers so that an installed FUSE helper cannot silently select a different implementation.

If the mount fails, read the next kernel messages:

```bash
journalctl -k -b -g 'ntfs|NTFS|bitlk|I/O error|reset|USB|usb' --no-pager
```

Generic “wrong fs type, bad option, bad superblock” output is not a diagnosis. See [[Fixing Un Mountable NTFS drive]] for hibernation, dirty state, MFT damage, and hardware errors.

### Normal writable access to a clean volume

Close a read-only mount/mapping before reopening it for writes. This is an alternative lifecycle, not a remount that bypasses the mapper's read-only state:

```bash
sudo umount /mnt/bitlk
sudo cryptsetup close bitlk_data

sudo cryptsetup open --type bitlk "$outer" bitlk_data
sudo mount -i -t ntfs \
    -o "uid=$(id -u),gid=$(id -g),dmask=0022,fmask=0133,windows_names" \
    -- /dev/mapper/bitlk_data /mnt/bitlk
```

Run `id` as the intended desktop account, not from a root shell. The tested masks produce directories `0755` and files `0644`. Never use a writable mount to bypass a Windows hibernation/Fast Startup refusal.

Opening the mapping for writes allows filesystem writes even though the unlock operation itself does not convert or reformat BitLocker. The old wording “cryptsetup can only read BitLocker” obscured this distinction.

## Clean Up

```bash
sudo umount /mnt/bitlk
sudo cryptsetup close bitlk_data
```

Unmount the inner filesystem before closing the outer mapping. If unmount fails as busy, resolve the users; do not treat a lazy detach as proof all I/O finished. An inactive mapper does not erase the on-disk data—it returns the volume to an inaccessible encrypted view until unlocked again.

## UDisks alternative

Use this as an alternative to the manual cryptsetup lifecycle:

```bash
udisksctl unlock --read-only -b "$outer"
```

Read its returned mapper path, then inspect and mount that actual device:

```bash
# Replace this with the exact mapper path reported by unlock.
inner='/dev/mapper/REPLACE_WITH_REPORTED_NAME'
lsblk -f "$inner"
udisksctl mount -b "$inner" -t ntfs -o ro
```

Cleanup:

```bash
udisksctl unmount -b "$inner"
udisksctl lock -b "$outer"
```

For ordinary writable desktop use, omit `--read-only` when unlocking and use appropriate supported mount options. UDisks runs through Polkit and the active session; exact authorization and default mount policy are session/configuration dependent. The `unlock --read-only`, `mount -t`, and `mount -o` arguments were confirmed from installed help, but the desktop BitLocker sequence was not executed here.

For a USB disk, optionally power off the whole physical drive **after** all its volumes are unmounted and mappings closed:

```bash
udisksctl power-off -b /dev/disk/by-id/REPLACE_WITH_WHOLE_USB_DISK_ID
```

Do not infer the whole-disk target by stripping arbitrary characters from a mapper or NVMe path; inspect the device tree.

## Persistent mounting

systemd crypttab supports the `bitlk` option:

```text
# /etc/crypttab — example, prompts/normal key acquisition depend on configuration.
bitlk_data UUID=REPLACE_WITH_OUTER_BITLOCKER_UUID none bitlk
```

```fstab
# /etc/fstab — substitute numeric IDs; shell expressions are not evaluated here.
/dev/mapper/bitlk_data /mnt/bitlk ntfs uid=REPLACE_UID,gid=REPLACE_GID,dmask=0022,fmask=0133,windows_names 0 0
```

The two records refer to different layers. An inner filesystem UUID is available only after unlocking. A named mapper makes the relationship explicit. Optional disks need deliberate dependency/timeout policy; an automount alone does not unlock an unconfigured container. See [[Mounting and fstab#Encryption and mount dependencies]].

This boot-time BitLocker configuration is documentation-checked, not a tested change to this machine. A data-volume example does not configure encrypted Windows boot, TPM integration, or Linux encrypted root.

## Packages and limits

Installed here: `cryptsetup`, `udisks2`, `ntfsprogs`, and the native kernel `ntfs` module. `ntfsfix`/`mkntfs` come from **`ntfsprogs`** in this package set. The separate FUSE package `ntfs-3g` is not required for native mounting.

A working Linux driver does not replace Windows filesystem repair. If NTFS is damaged, decide between copying data, imaging unstable hardware, and filesystem repair using [[Fixing Un Mountable NTFS drive]]. If the BitLocker header/protector state itself is damaged or unsupported, NTFS tools cannot repair that outer layer.

## Quick Reference

| Task | Command |
|---|---|
| Inspect outer header | `sudo cryptsetup bitlkDump "$outer"` |
| Read-only mapping | `sudo cryptsetup open --type bitlk --readonly "$outer" bitlk_data` |
| Inspect active mapping | `sudo cryptsetup status bitlk_data` |
| Probe inner filesystem | `sudo blkid /dev/mapper/bitlk_data` |
| Read-only native NTFS mount | `sudo mount -i -t ntfs -o ro /dev/mapper/bitlk_data /mnt/bitlk` |
| Unmount | `sudo umount /mnt/bitlk` |
| Close mapping | `sudo cryptsetup close bitlk_data` |

## References

Installed `cryptsetup-open(8)`, `cryptsetup-bitlkDump(8)`, `crypttab(5)`, and `udisksctl` help. [[Disk Verification]] documents the absence of a real BitLocker test device and the separate native-NTFS fixture results.
