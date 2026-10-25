---
title: "Windows VM verification and known limits"
tags:
  - kvm
  - manual
  - windows
reviewed: 2026-10-25
---

# Windows VM verification and known limits

[[+ MOC KVM|KVM home]] · [[+ MOC Windows Installation Through Virt Manager|Windows route]]

Run checks for the features you selected. Configuration success, guest-agent
success, visible pixels and hardware rendering are different forms of evidence.
A disabled/unselected optional component need not be repaired into existence.

## Checks after installation

| Layer | Verify |
|---|---|
| Host | KVM validation, named network, mounted storage, expected native/VFIO drivers |
| Boot | Windows reaches desktop, correct capacity, healthy Device Manager |
| Management | QGA ping and a separately requested graceful shutdown/restart |
| Desktop | Sign in normally; see the actual LG/SPICE desktop, move pointer and type |
| Clipboard | Copy distinct Unicode/multiline text both ways in simple editors |
| Shared files | Create/read/rename both ways, compare hashes and check ownership |
| RDP/SSH | Correct edition/account/service and a real authenticated connection |
| Physical GPU | Native driver/PnP healthy, hardware workload passes, reset reuse/logs checked |
| Memory | Correct fixed capacity or verified target/physical reclamation, as selected |
| Backup | Domain/firmware/TPM verification plus separately preserved guest disks |

For the scripted host/guest diagnosis:

```sh
python3 ~/user_scripts/dusky_vm/passthrough/windows/50_win.py doctor --vm win11 --guest
python3 ~/user_scripts/dusky_vm/passthrough/windows/50_win.py repair --vm win11 --guest
```

Doctor is read-only. Repair is a separate mutating request that fixes supported
known drift; it does not download/reinstall every driver or guarantee self-repair
of hardware, storage, GPU reset or arbitrary Windows faults. It follows saved
access choices. See its `--help`/README for optional SSH, JSON and pending checks.

## Observed in this session — 2026-10-25

Host: Intel i7-12700H/i915 and NVIDIA RTX 3050 Ti Laptop, kernel
7.3.0-rc7-dusky-battery, systemd 262.1, Python 3.15.1, QEMU 11.1.2, libvirt 12.8,
virtiofsd 1.14.0. Windows 11 build 26300. These are observations, not version pins
or the final ISO manifest.

- RAM-backed VM was shut down, copied to persistent media and checked with qcow2
  checks and matching complete-file SHA-256 hashes before using the copy.
- Bind and return-to-Linux host boots passed configuration/driver checks. Native
  return restored NVIDIA video, its audio driver and Intel display. The early
  boot audit briefly ran before audio initialized; a later check passed.
- Windows NVIDIA 621.01 installed with a valid publisher signature. No old local
  577 package was used. Future installs must choose the current appropriate driver.
- Hardware Direct3D readback stress: 81,920 operations and 245,760 sampled pixel
  checks passed in 34.06 seconds across ten runs, with 128 MiB texture allocation
  per run. This is not full VRAM, gaming, thermal or physical-audio qualification.
- Three clean VM shutdown/start cycles passed rendering checks; the user
  confirmed Looking Glass's visible desktop after the last cycle.
- **One DMAR DMA-write fault occurred during rapid guest shutdown.** Its cause
  remains unresolved. Later ordinary shutdown and the inspected return-to-host
  boot added no such fault. Passing rendering does not erase that finding.
- VirtIO-FS: 16 MiB random files each way matched SHA-256; Unicode rename and
  owner checks passed; 500 concurrent replacement/read operations passed;
  service and drive returned after Windows restart.
- Ballooning without hostdevs: 8 → 6 → 4 → 8 → 6 → 8 GiB worked with VirtIO-FS.
  Four GiB target reclaimed about four GiB of QEMU RSS/host shared memory. A
  512 MiB application buffer retained its full hash through resizing. Fixed
  eight GiB was restored afterward.

The Windows/shared notes review passed 43 Windows regressions, four storage-
identity/profile-order checks and 13 profile-runner tests (60 distinct tests).
The corrected account discovery also reported this host's actual libvirt-qemu
UID/GID. The notes' internal links, shell syntax, PowerShell parsing and complete
XML templates were checked separately; no guest policy/driver changes or host
reboot were performed for this documentation pass.

Fixture tests cover hardware/vendor choices and failures but do not replace
other physical machines. No complete fresh ISO reinstall, physical eight-GiB
host, running 50-GiB guest, AMD/Intel physical passthrough, or ballooning with
VFIO was empirically certified. Final supported kernel/ISO versions remain to
be validated. Shell/XML syntax validation does not mean every documented
mutating command was run against this host.

## Evidence and repeatability

Persistent VM artifacts are under `/mnt/media/virt_vms/win11/` in the dated
vfio-review, share-review and memory-review directories. Those are this machine's
records, not paths new users need to create. The script source has
`VFIO_LIVE_TEST_NOTES.md`, `windows/guest/LIVE_TEST_NOTES.md` and regression suites.
This notes review retains original notes/diffs and validation results under
`~/.local/state/dusky/kvm-notes-review-20261025`. A second documentation pass is
recorded in `~/.local/state/dusky/kvm-notes-second-pass-20261025`: fresh-host
ordering, tmpfs/fstab syntax, offline bridge-profile generation, a scratch qcow2
copy/hash check, and isolated Windows key-file encoding/deduplication checks.
The last check tests file preparation, not an authenticated SSH connection.
It did not change the live network, VM hardware or actual SSH key files.

Reproduce the selected workload checks after meaningful kernel, guest driver,
IDD/client or hardware changes. Avoid an unconditional “future-proof on every
machine” claim from one laptop's successful test.
