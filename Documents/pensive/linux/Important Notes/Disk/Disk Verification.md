# Disk Notes — Verification Record

> [!info] Dated evidence, not permanent hardware assumptions
> Checked on **2026-10-29** on this Arch installation. This page separates the observed machine, executed tests, inspected interfaces, and procedures that could not be tested. Reusable commands in the other notes avoid this machine's UUIDs and account paths.

## What changed in the notes

The five original filenames were retained so existing Obsidian links can keep resolving. Material is now organized as:

| Note | Role |
|---|---|
| [[Storage Stack]] | Layers, naming, sectors, boot/mount model |
| [[Disk]] | Inspection, formatting concepts, capacity, monitoring, benchmarks, health, TRIM |
| [[Mounting and fstab]] | Mount lifecycle, options, ownership, systemd and encryption dependencies |
| [[BTRFS]] | CoW/NOCOW, compression, snapshots, send/receive, integrity, ENOSPC, resize, swap |
| [[Bitlocker]] | BitLocker outer layer and access lifecycle |
| [[Fixing Un Mountable NTFS drive]] | NTFS failure diagnosis, copying, imaging, repair limits |
| [[Local Drive Tools]] | Relevant local helper entry points and concrete limitations |
| [[Disk Verification]] | This evidence record |

The drive scripts, fstab, crypttab, boot configuration, service enablement, and physical partition layouts were not edited. Tests created disposable storage objects and temporary files; those objects were closed/unmounted/detached after testing.

## Installed environment

| Component | Observed version / state |
|---|---|
| Running kernel | `7.3.0-rc7-dusky-battery` |
| util-linux | `2.42.4-1` |
| systemd | `262.1-1` |
| btrfs-progs | `7.3-1` |
| cryptsetup | `2.8.8-1` |
| UDisks | `2.11.2-1` |
| smartmontools | `7.5-2` |
| nvme-cli / libnvme reported by CLI | `3.1` / `3.1` |
| sysstat | `12.8.0-1` |
| GNU coreutils | `9.12-2` |
| GNU Bash | Package `5.3.20-1` |
| NTFS utilities/library | `ntfsprogs` and `libntfs-3g` `2026.9.28-1` |
| e2fsprogs | `1.47.4-1` |
| dosfstools / exfatprogs | `4.2-5` / `1.4.3-1` |
| compsize | `1.5-2` |
| DwarFS | `0.15.8-1` |
| Python selected by normal `python3` PATH lookup | `/usr/local/bin/python3`, version `3.15.1` |
| Arch `python` package / `/usr/bin/python3` target | Package `3.14.8-1`, symlink to `python3.14` |

`fio`, `ddrescue`, `ntfs-3g` (the FUSE driver package), `ncdu`, and `lvm2` were not installed. NTFS utilities **were** installed through the separate `ntfsprogs` package. `pacman -Qo /usr/bin/ntfsfix` confirmed the actual provider.

For test execution only, the sync database's fio `3.42-1`, GNU ddrescue `1.30-2`, and the library package needed by that fio binary were downloaded and extracted beneath `/tmp`. They were not installed with pacman and did not alter the system's package database. fio initially failed to load without `libgfapi.so.0`; using the extracted GlusterFS libraries for that child process resolved it, and all reduced-size fio jobs then passed.

The running kernel is a **release candidate**. These checks establish behavior of that exact build, not a shipped final Linux 7.3 ISO. The ISO's final kernel, companion packages, Python path, filesystem features, and rescue media still need release verification. “Latest” is time-dependent; no note can guarantee future tool behavior without checking the versions then installed.

### Kernel filesystem support

Read from `/proc/config.gz` and corroborated with module information/mount tests:

```text
CONFIG_BLK_DEV_LOOP=m
CONFIG_EXFAT_FS=m
CONFIG_NTFS_FS=m
# CONFIG_NTFS3_FS is not set
CONFIG_BTRFS_FS=y
CONFIG_FUSE_FS=m
```

`modinfo ntfs` identified a read/write filesystem driver. Mounting/writing a temporary NTFS image succeeded with `mount -i -t ntfs`. Loop and exFAT were also exercised. The old machine section claiming no loop/exFAT support and an available `ntfs3` module was stale.

## Observed storage layout

Transient controller/device numbers below describe **this boot only**; they are not templates for automation.

| Observed device/layer | Role |
|---|---|
| Intel SSDPEKNU512GZ namespace, about 476.9 GiB | Partitioned system drive |
| Its 3 GiB LUKS2 partition | Inner ext4 `browser`, mounted at the account's `.config/mozilla` |
| Its roughly 1.3 GiB FAT32 partition | ESP at `/boot` |
| Its 120 GiB Btrfs partition, label `DUSKY_ROOT` | Root/home and multiple separately mounted subvolumes |
| Samsung SSD 980 1TB namespace, about 931.5 GiB | A 505 GiB LUKS2 partition with inner ext4 at `/mnt/media` |
| `zram0`, 20 GiB logical capacity | Active swap |
| `/swap/swapfile`, 4 GiB file | Exists, but was not active; its fstab swap entry was commented |

There was no active `zram1` filesystem in `lsblk`/`findmnt`. A configured path mentioning it is not evidence that such a device is currently present.

`bootctl status` identified UEFI and systemd-boot `262.1-1-arch`. The running command line explicitly selected Btrfs `subvol=/@`. No boot entry or firmware policy was changed.

Both active LUKS2 mappings reported 512-byte encryption sectors and these flags:

```text
discards no_read_workqueue no_write_workqueue
```

Those observed flags do not establish a default. The drive manager explicitly requests related flags, and the clean-container tests below demonstrated that neither default manual nor default systemd activation supplied discard by itself.

### Btrfs subvolumes and allocation

The read-only list showed **19** subvolumes, all with top-level/parent ID 5:

```text
@                       @home
@snapshots              @home_snapshots
@var_log                @var_cache
@var_tmp                @var_lib_machines
@var_lib_portables      @var_lib_libvirt
@var_lib_mysql          @var_lib_postgres
@swap
@store_0_docker         @store_0_containerd
@store_0_podman         @store_1000_docker
@store_1000_podman       @store_1000_libvirt_images
```

The current mounts showed `noatime,compress=zstd:3,ssd,discard=async,space_cache=v2` with explicit subvolume selections. This review did not remove redundant-looking options from fstab.

At sampling time, the 120 GiB filesystem had about **1 MiB device-unallocated space**, `Data,single` about **84% used**, and `Metadata,DUP` about **53% used**. Estimated/statfs free space was roughly **18 GiB**, varying with live activity. All five Btrfs device error counters were zero at that observation.

This is direct evidence that almost-zero unallocated device space alone does not prove an allocator emergency. No balance, scrub, device add/remove, or resize was performed on this live filesystem.

### Services and maintenance state

`fstrim.timer`, `smartd.service`, and `dusky_snapshot.timer` were **disabled and inactive** when checked. The old claim that daily paired snapshots were currently scheduled was not supported by the timer state. Retention configuration and the external snapshot manager were not revalidated in this task.

The packaged fstrim timer specifies weekly operation with scheduling accuracy/randomized delay and persistence. No timer/service was enabled or started. `fstrim --dry-run --all --verbose` was exercised; live discard was not issued by this review.

### Health reports

Read-only SMART/NVMe reports showed:

| Device | Critical warning | Media/data-integrity errors | Reported percentage used |
|---|---:|---:|---:|
| Intel SSDPEKNU512GZ | `0x00` | `0` | `32%` |
| Samsung SSD 980 1TB | `0x00` | `0` | `10%` |

These are telemetry observations, not a guarantee of future health or a remaining-lifespan calculation. `nvme id-ctrl`, SMART logs, and power-management feature queries were read. No self-test was started and no power/firmware setting changed.

## Empirical checks: 32/32 filesystem/encryption checks passed

The harness used newly created sparse image files, loop devices, and a private mount namespace. Write-capable filesystem/partition/encryption commands targeted only those disposable devices. Cleanup closed mappers, unmounted test trees, detached loop devices, and removed their backing images.

| Group | What was exercised and observed |
|---|---|
| NOCOW inheritance | `cp -a --reflink=never` inherited destination `+C`; same-subvolume `mv` preserved CoW; cross-subvolume GNU `mv` inherited destination NOCOW |
| Reflinks | Matching NOCOW source/destination reflink succeeded; CoW-to-NOCOW reflink was rejected |
| Inode compression | `none` set `m`; empty property cleared it; `zstd` on NOCOW was rejected |
| Snapshot boundaries | Nested subvolume contents were absent from the parent snapshot; NOCOW files were present |
| Send/receive | Full send/receive succeeded; incremental send/receive succeeded and contained the newly added file |
| Direct I/O/compression | Buffered 4 MiB zeros compressed; aligned direct-written zeros did not compress on this 7.3 build |
| Btrfs commands | Extent accounting, defrag with positive/negative zstd levels, filesystem shrink/grow, empty-group balance, foreground no-repair scrub, swapfile creation and offset mapping succeeded |
| NTFS no-action check | Entire image SHA-256 unchanged after `ntfsfix -n` |
| Native NTFS | Read/write mount, explicit ownership, file `0644` and directory `0755` masks, persistence after remount, and read-only write rejection passed |
| NTFS `windows_names` | Mount accepted; creating `bad:name`, `CON`, and `trailing.` was rejected with `EINVAL` in the fixture |
| exFAT | Format, native mount, write, and unmount succeeded |
| GPT/Btrfs order | Filesystem shrink preceded partition shrink; partition/kernel growth preceded filesystem growth; kernel byte sizes matched |
| Actual local resize helper | Status, 768→704 MiB shrink, and 704→768 MiB grow succeeded on the GPT fixture with byte checks |
| Clean LUKS defaults | Plain cryptsetup open had no discard flag; systemd-cryptsetup attach with `luks` also had no discard flag |
| LUKS persistence | Opening with `--allow-discards --persistent` set the flag; closing and ordinary reopening preserved it |

The 32 count includes individual assertions and grouped command-path success checks; it is not 32 independent hardware models or 32 proofs of general filesystem correctness.

### Failures during test development were investigated

An initial expectation that native NTFS would reject `windows_names` was **wrong**: the mount succeeded. Follow-up file-creation tests established that the option was enforced. The notes use the observed result, not the initial expectation.

An initial GPT fixture attempted a very small Btrfs shrink (128 MiB to 112 MiB), which the kernel rejected. The test was resized to a representative supported range and passed. This is why the notes do not promise arbitrary shrinking merely because file content would appear to fit.

## Empirical checks: 8/8 tool checks passed

| Check | Result / scale |
|---|---|
| fio sequential write | 16 MiB file, io_uring, direct I/O, depth 16, refill/random-buffer flags and final fsync |
| fio sequential read | Same initialized file, readonly mode, no file creation |
| fio mixed random workload | 16 MiB file, 4 KiB requests, 70/30 read/write, depth 32, one second |
| ddrescue first pass | Healthy 1 MiB source file to image with mapfile, `-n` |
| ddrescue resume/direct input | Same source/image/mapfile with `-d -r1` |
| ddrescue content verification | Source and output SHA-256 matched |
| iostat | Finite `-dx -y` interval sample |
| pidstat | Finite per-process I/O interval sample |

These validate interfaces and a working local code path. They are **not physical-drive performance measurements**, sustained-write tests, failing-sector recovery tests, or proof of recoverability after corruption. The 4 GiB/30-second examples in [[Disk]] are user-run workloads, not measured results from this review.

## Other read-only validation

The original five notes were read. Relevant local helper implementations, command constructions, entry points, configuration relationships, and the DwarFS toolkit README were consulted. Full correctness testing of all drive-script actions was outside this documentation task.

Executed/read:

- Command paths, installed package versions/providers, kernel configuration, module information, current mounts, fstab, swap state, boot status, and command line.
- `findmnt --verify --verbose`: **success, no errors or warnings** on the current fstab.
- Btrfs filesystem/device usage, subvolume list, and error counters.
- Current LUKS mapping status, SMART/NVMe identification/health reports, and power-management feature reads.
- Relevant `--help` output and installed manual pages for the documented tools.
- Local drive-manager help and real `status`, resize-helper help/status, formatter help, drive-health help, and storage-write-monitor help.

Final document validation passed: **100 Bash blocks parsed successfully in both Bash and zsh**, and **74 sibling Obsidian links resolved, including their heading targets**. Git whitespace checks passed for the tracked changes. New notes were checked directly because ordinary Git diff does not show untracked contents. Syntax checks do not prove command semantics, permissions, or success on an unavailable device.

## Material corrections to the old notes

| Old claim/advice | Verified correction |
|---|---|
| Loop/exFAT absent; use `ntfs3` | Current build has loop/exFAT modules and native read/write `ntfs`; `ntfs3` is not built |
| `ntfs-3g` is the installed provider of ntfsfix | `pacman -Qo` reports `ntfsprogs` here |
| Arch recovery package is `gddrescue` | Sync database and extracted working package are `ddrescue` |
| systemd enables LUKS discard by default | Clean manual and systemd activation both lacked discards; explicit/persistent flags matter |
| NOCOW cannot reflink | Matching NOCOW policies reflinked successfully |
| NOCOW avoids snapshots | Snapshot retained the NOCOW file |
| `subvolume list -a` shows pending deletions | Installed help identifies `-d` as that filter |
| Root/home snapshot pair is atomic | Separate subvolumes require separate operations; no cross-subvolume atomicity established |
| Setting the default subvolume selects any boot root | Explicit boot/fstab `subvol=` selection overrides that default |
| Btrfs zstd supports only positive levels | Installed interface supports negative levels too; negative-level defrag succeeded |
| Old direct-write compression behavior applies unchanged | Current 7.3 fixture distinguished buffered compression from uncompressed direct writes |
| Near-zero unallocated Btrfs space means rescue is needed | Live metadata had substantial free capacity inside allocated groups |
| Any balance failure can be dismissed as minor | Errors need diagnosis; automatic escalation is not justified |
| ntfsfix `-b -d` clears Fast Startup/hibernation | Those options clear recorded bad sectors and dirty state, not general hibernation recovery |
| Mapper exists, therefore BitLocker is definitely fine | Mapping existence does not prove full data integrity or exclude target/driver/options errors |
| `fsck.ntfs` is always ntfsfix | No `fsck.ntfs` is installed here; inspect actual package behavior |
| Resize helper has a fixed 500M margin | Current code uses exact bounds/validation and Btrfs's resize result, not that described margin |
| Every storage medium has one universally best tuning recipe | Removed unmeasured universal claims about autodefrag, discard, commit intervals, and dm-crypt gains |

## What remains documentation-only

No real BitLocker device, damaged NTFS volume, Windows/WinRE session, MD/LVM stack, failing medium, or genuine metadata-exhaustion scenario was available for testing. Desktop authorization/mount defaults, file-manager visibility, actual suspend/hibernate/resume, shutdown-ordering diagnostics, boot-time crypttab prompts, off-machine send transport, and every local TUI/menu action were not exercised.

The review did not run destructive operations against your physical devices, enable maintenance timers, repair a filesystem, change a boot parameter, or install packages. Documentation examples that perform those actions are clearly identified as procedures to adapt.

## Evidence and reference trail

The durable results and limitations are summarized on this page. Raw captured manuals, help output, original note copies, harnesses, JSON results, and logs were retained temporarily at:

```text
/tmp/disk-notes-review-20261029/
```

That location can be cleaned by the OS; it is not a permanent backup. Important result files are `fixtures-results.json`, `fixtures.log`, `tool-smoke-results.json`, `tool-smoke.log`, `system-readonly.txt`, `health-readonly.txt`, and `services-and-status.txt`.

Installed manuals were preferred for the actual command interfaces. Primary online references consulted where relevant:

- [Linux native NTFS documentation](https://docs.kernel.org/filesystems/ntfs.html) for the modern driver overview; actual supported options were also tested locally.
- [Btrfs documentation](https://btrfs.readthedocs.io/en/latest/) alongside the installed 7.3 manuals.
- [systemd crypttab reference](https://www.freedesktop.org/software/systemd/man/latest/crypttab.html) alongside the installed 262 manual and clean-container activation tests.
- [fio documentation](https://fio.readthedocs.io/en/latest/fio_doc.html) alongside the extracted 3.42 package documentation and working reduced-size jobs.
- [Microsoft chkdsk reference](https://learn.microsoft.com/en-us/windows-server/administration/windows-commands/chkdsk) and [manage-bde unlock reference](https://learn.microsoft.com/en-us/windows-server/administration/windows-commands/manage-bde-unlock) for the Windows commands, which remain unexecuted here.

Prefer a freshly checked installed interface over either a stale note or a moving upstream “latest” page. When a later update changes an interface, record the version and repeat the focused test that matters.
