# Local Drive Tools

> [!abstract] Scope
> A guide to the relevant helpers under `~/user_scripts/drives/`, based on entry points, help output, and selected command paths inspected on **2026-10-29**. These scripts are local conveniences, not replacements for understanding [[Storage Stack]], [[Disk]], [[Mounting and fstab]], and [[BTRFS]]. Their source was not changed in this notes review.

## Choose the helper by operation

| Need | Helper | Validation in this review |
|---|---|---|
| Configured drive status/unlock/lock | `drive_manager/drive_manager.py` | Help and real read-only `status` executed; relevant mount/unlock code inspected |
| Live SMART + I/O display | `dusky_disk_monitor_io.py` | Relevant startup/telemetry source inspected; interactive TUI not exercised |
| Health/gap/discard reporting | `drive_health/dusky_drive_health.py` | Help and action selection inspected; no raw gap scan/discard executed |
| Edit fstab through the local TUI | `fstab/tui_fstab.py` | Schema and entry-point integration inspected; no fstab edits |
| Format a volume | `format/dusky_formater.py` | Help executed; relevant planning/entry-point code inspected; no live format |
| Resize direct GPT/Btrfs | `format/resize_btrfs.py` | Help, live read-only status, and real shrink/grow on a disposable image |
| Display compression statistics | `btrfs_zstd_compression_stats.sh` | Source inspected; underlying compsize tested on a fixture |
| Record device/process writes | `monitor/storage_write_monitor.py` | Help and counter/database paths inspected; daemon not started |
| NTFS repair wrapper | `ntfs_fix.sh` | Source read; not recommended as default recovery workflow |
| Emergency Btrfs allocator rescue | `btrfs_fix_enospc_metadata_exaustion.sh` | Source read; not run on live root or genuine ENOSPC |
| DwarFS image toolkit | `file_compression/` | Toolkit README read; refer to its own workflows |
| Cache-unmount diagnosis | `diagnostics/` | Script/service read; no installation or shutdown test |

The table intentionally distinguishes inspected source from executed behavior. No claim here establishes that every menu action or hardware layout works.

## Python environment

Several helpers target Python 3.15 features. On this system, the normal PATH selected Python 3.15.1, while Arch's installed `python` package was 3.14.8. Do not assume `/usr/bin/python3` and `python3` resolve to the same interpreter.

```bash
command -v python3
python3 --version
/usr/bin/python3 --version
```

The verified examples below use `python3` from the intended environment. A fresh ISO must provide the documented interpreter and dependencies consistently. This review did not change PATH, symlinks, Python installations, or script code.

## Drive manager

```bash
python3 "$HOME/user_scripts/drives/drive_manager/drive_manager.py" --help
python3 "$HOME/user_scripts/drives/drive_manager/drive_manager.py" status
```

Run it as the desktop account; it handles its required privilege operations. It supports:

```bash
# Mutating lifecycle examples; replace the configured target name.
python3 "$HOME/user_scripts/drives/drive_manager/drive_manager.py" unlock DRIVE_NAME
python3 "$HOME/user_scripts/drives/drive_manager/drive_manager.py" lock DRIVE_NAME
```

`-c PATH` selects an explicit TOML configuration. `set-password DRIVE_NAME` stores credentials through its Secret Service/keyring workflow; it is not a status query. Use the help/configuration actually installed rather than the old note's assumption that all volumes are opened with keyfiles.

The reviewed native-NTFS path uses `mount -i -t ntfs`. Its non-rotational encryption path explicitly requests discards, and the LUKS path adds dm-crypt workqueue flags. These explicit choices help explain the active mappings' settings; they are not evidence that systemd enables them by default.

Lock/unlock can involve mounts, encryption mappings, configured hooks, process handling, and a scheduled TRIM operation. Treat them as actual drive-management operations. Only `status` was exercised against this machine during the review.

## Btrfs resize helper

```bash
python3 "$HOME/user_scripts/drives/format/resize_btrfs.py" --help
python3 "$HOME/user_scripts/drives/format/resize_btrfs.py" --mountpoint / --status
```

The supported path is read-write, single-device Btrfs directly on GPT. It is not a LUKS/LVM/MD resizer. It saves a partition-table backup, uses the on-disk logical-sector size, preserves starts, checks overlap, updates the kernel partition bounds, and verifies them before filesystem growth.

The displayed delta flags `--shrink SIZE` and `--grow SIZE` were exercised on a loop-backed GPT fixture, including byte-exact kernel-size checks. Read [[BTRFS#Resizing]] before using either on real data. A table backup is not a backup of the filesystem contents or a guarantee of power-loss recovery.

## Formatter and fstab editor

```bash
python3 "$HOME/user_scripts/drives/format/dusky_formater.py" --help
```

Formatting is destructive. The formatter's advertised filesystem choices are **not** proof that every kernel driver and userspace formatter is installed. Its CLI includes device/filesystem selection, optional GPT creation, labels, optional LUKS2 encryption, and a no-confirmation mode. Read the printed command plan and the installed tool interfaces for a real format task. Help was the only formatter action executed here.

`fstab/tui_fstab.py` integrates with the local Dusky TUI engine. Its schema uses the current native `ntfs` type and provides a Btrfs CoW switch. Remember that `nodatacow` is broad filesystem mount policy; toggling it on one displayed subvolume does not isolate that policy to that subvolume.

After any deliberate editor change, run `findmnt --verify`, reload systemd, and test the affected mount as described in [[Mounting and fstab]]. This review did not launch a writing editor session.

## Health, write monitoring, and compression statistics

### Health tool

```bash
python3 "$HOME/user_scripts/drives/drive_health/dusky_drive_health.py" --help
```

Its action flags differ materially:

| Flag | Meaning |
|---|---|
| `--mock` | Demonstration data |
| `--scan` | Actual reads sampling unallocated partition-table ranges |
| `--dry-run-discard` | Report planned discard ranges |
| `--execute-discard` | Actually discard selected unallocated ranges |
| `--device PATH` | Select a physical device |

Reading zeros from an unallocated host-address range does not reveal whether the SSD's internal FTL has physically erased NAND. Partition-table free space is not filesystem free space. Neither a zero sample nor a discard report measures “remaining lifespan” or proves a particular over-provisioning gain. No execution/discard mode was run here.

### Storage write monitor

```bash
python3 "$HOME/user_scripts/drives/monitor/storage_write_monitor.py" --help
```

The monitor writes a SQLite database under the invoking account's `~/.local/share/storage_write_monitor/`. Its `--summary HOURS` reads accumulated history; a background/continuous sample loop creates and updates that history. Starting it with `sudo` changes the account/home context and can change which database is used.

The reviewed main routine accepts `--daemon`, but that flag does not fork or select a different sampling implementation; the monitor loop still runs in the foreground. A service/process supervisor is what backgrounds/manages it. Do not assume the flag independently creates a managed daemon.

Kernel `/proc/diskstats` sectors are counted in 512-byte units. Its device totals include different stack layers, so summing whole disks, partitions, and dm devices can double-count. Its process I/O sampling is also subject to permissions and short-lived processes. Use the reports as observed counter deltas, not exact physical NAND writes.

### Compression-statistics wrapper

`btrfs_zstd_compression_stats.sh` discovers mounts, elevates, can install missing packages, and scans via `compsize`. It captures scan errors and computes a rounded human-readable summary. It is therefore not an ideal source for byte-exact accounting or a test that every target was scanned successfully.

Prefer the underlying tool when learning or validating a specific tree:

```bash
sudo compsize -b -x /path/to/data
```

Its savings percentage describes the scanned extents' compression ratio. It is not the percentage of total drive capacity reclaimed. See [[BTRFS#Measure stored extents]].

## NTFS wrapper: a concrete limitation

`ntfs_fix.sh` discovers NTFS/encrypted candidates and may unlock a temporary mapper. Its repair action invokes **`ntfsfix -b -d`**. It then prints that Fast Startup/hibernation flags were cleared.

The command does not justify that message. The installed manual defines `-b` as clearing the bad-sector list and `-d` as clearing the dirty flag under the tool's conditions. Neither is a general hibernation repair. Its dependency message also refers to the older package arrangement rather than this system's `ntfsprogs` provider.

Use the manual diagnosis and recovery sequence in [[Fixing Un Mountable NTFS drive]]. The wrapper was not executed or modified in this task; this note records why it should not be treated as a universal one-step repair.

## Emergency Btrfs helper: a concrete limitation

`btrfs_fix_enospc_metadata_exaustion.sh` can automatically choose a workspace, allocate a loop-backed image, add it to Btrfs, run progressively broader balance passes, and retry device removal.

It tracks whether the added device remains attached and warns against removing its backing image in that state. However, the reviewed balance path suppresses command diagnostics and labels failures as minor warnings before continuing. Failure is not necessarily minor: it can mean insufficient relocation space or another filesystem error. The automatic workspace selection and eventual evacuation also need to be valid for the actual topology and available capacity.

Do not launch it merely because device unallocated space is near zero. Diagnose complete data/metadata usage first. Its genuine-ENOSPC rescue lifecycle and interruption/reboot behavior were not tested. [[BTRFS#Temporary extra device: advanced recovery, not routine maintenance]] explains the dependency it creates.

## DwarFS toolkit

DwarFS builds compressed read-only images, useful for mostly static trees. This differs from Btrfs transparent compression: editing a DwarFS image's visible tree requires a separate writable overlay or rebuilding/extracting it.

The local toolkit's `file_compression/README.md` is its detailed operational reference. It covers `mkdwarfs_auto.sh`, profiles, image verification, mounting, extraction, and optional launch integration. Example from the toolkit directory:

```bash
cd "$HOME/user_scripts/drives/file_compression"
bash 03_tools/mkdwarfs_auto.sh "$HOME/Documents" --profile balanced
```

That operation creates a new image and can consume substantial CPU, RAM, and storage; it was not run here. Keep output outside the input tree and read the toolkit's existing-output and verification behavior before building an image. A compressed archive's existence alone is not proof of a restorable backup.

## Cache-unmount diagnostics

The script under `diagnostics/` collects mount/process/loop/namespace information if `/var/cache` remains mounted during the intended shutdown ordering. The service orders it around the cache mount's stop and `umount.target`.

It records diagnostics; it does not unmount, kill processes, or repair a filesystem. No service was installed/enabled and no shutdown was triggered in this review. Use its recorded log to identify actual holders before changing shutdown behavior.

## Review boundary

This was a documentation task with focused empirical checks, not a certification of every local script. Entry points and directly relevant command paths were inspected; unrelated UI, remote FTP operations, hooks, and every failure branch were not exhaustively exercised. Use [[Disk Verification]] for the exact executed checks and current machine facts.
