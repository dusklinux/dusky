# Reinstalling Arch: Windows 11, Looking Glass and RDP

This is the host-side run order. [guest/README.md](guest/README.md) covers Windows.
The scripts configure the current Looking Glass IDD stack. Keep its Linux client
and Windows installer from the same upstream build when preparing offline media.

## Before reinstalling

Keep your offline files on persistent storage. A host reboot loses tmpfs disks.
If retaining an existing VM, preserve its powered-off disk, domain XML, UEFI
NVRAM and virtual TPM state together. The XML/configuration backup script does
not back up the guest disk. A new test VM can instead be created from scratch.

Prepare these inputs:

- Windows 11 Pro/Enterprise/Education ISO and VirtIO-Win ISO.
- Matching Looking Glass Linux source/package and Windows IDD installer from
  [the same download build](https://looking-glass.io/downloads).
- The complete `guest` directory, with `looking-glass-idd-setup.exe` beside
  `00_bootstrap.ps1` and Python 3.15+ in `python/python.exe`.
- Offline OpenSSH media/ZIP if SSH is wanted. It is optional: `-SkipSsh` still
  allows Looking Glass, the QEMU guest agent and RDP.

The tested guest runtime was the official Python embeddable ZIP, extracted into
`guest/python`. Keep the entire extracted runtime, not just `python.exe`.
You may package this guest directory into a data ISO and attach it through
virt-manager's **Add Hardware → Storage → CD-ROM**. With `xorriso` installed:

```sh
xorriso -as mkisofs -J -R -V DUSKY_GUEST -o /path/to/windows-guest.iso /path/to/prepared/guest
```

## Host requirements

Use the supported baseline: kernel 7.3+, systemd 262+, Python 3.15+ and Hyprland
on Wayland. Verify the final ISO's actual versions. Stock Arch packages on the
test machine still included Python 3.14 and kernel 7.2; those do not establish
that the planned ISO baseline is ready. Python's `rich` module must be installed
for the interpreter running these scripts, including after sudo elevation.

Phase 1's [package list](../05_virtio_iso.py) is the host hypervisor dependency
list. For offline use, include those packages plus `freerdp`, the matching
Looking Glass client and its runtime dependencies in the ISO/package set.
The setup does not turn an incomplete package cache into an offline installer.
If host packages are missing, prepare a consistent Arch package set first.

Phase 25 accepts an already installed current client. For offline use, install
the paired client beforehand and use `25_looking_glass.py --no-packages` when
running that phase directly. Without that option, missing components may use
pacman/AUR. An independently updated `looking-glass-git` is not proof that its
build still matches a previously saved Windows installer.

For a source build, use the official archive with submodules included. The
Wayland build used `base-devel`, `cmake`, `ninja`, `fontconfig`, `fuse3`,
`libglvnd`, `nettle`, `libxkbcommon`, `wayland`, `libdecor`, `spice-protocol`,
`libpipewire`, `libpulse`, `libsamplerate`, `usbredir`, `libunwind`, `elfutils`
and `ttf-dejavu`. Install these from the same Arch snapshot before going offline.
Build/install the paired source as follows, replacing the source path:

```sh
cmake -S /path/to/paired-source/client -B "$HOME/.cache/dusky-lg-build" -G Ninja -DENABLE_X11=OFF -DENABLE_WAYLAND=ON -DOPTIMIZE_FOR_NATIVE=OFF
cmake --build "$HOME/.cache/dusky-lg-build" --parallel 3
sudo cmake --install "$HOME/.cache/dusky-lg-build"
```

## Run host setup, then deploy once

Run from a terminal as your desktop user:

```sh
cd ~/user_scripts/dusky_vm/passthrough
python3 000_dusky_vm.py --windows --dry-run
python3 000_dusky_vm.py --windows --reset-and-run --setup-only --manual
```

Use `--reset-and-run` for a fresh Arch installation, especially when restoring
your old home directory: old user-owned receipts must not skip setup on a new
host. After an ordinary interrupted setup, rerun with `--setup-only --manual`
without resetting to resume. Complete any requested host reboot before creating
a RAM-backed VM. Refresh your login session if new group membership is needed.

GPU isolation has its own yes/no question (default No). Leave it declined for
software IDD rendering. Resuming remembers the choice; resetting the profile
opens the choices again. Phase 25 separately asks whether to set up Looking
Glass and FreeRDP; Phase 60 skips client configuration when LG is declined.
For VirtIO media, an existing staged ISO or installed ISO package is reused;
otherwise supply your local ISO path at the prompt. Enter selects a download.
Phase 25 creates shared memory; Phase 60 writes the desktop user's client config.

Create the VM separately, so rerunning host setup does not create another VM:

```sh
python3 000_dusky_vm.py --windows --deploy-only
```

Deployment asks for Looking Glass + RDP, LG only, RDP only, or SPICE console
only. GPU passthrough is independent. Disk/network devices can use VirtIO or
SATA/e1000e; missing VirtIO media requires an explicit choice to retain devices
whose drivers must already be available. Only LG selections get IVSHMEM; all
Windows selections retain the setup console and guest-agent channels. Each
domain records these choices for diagnosis. `--dump-only` previews XML without creating
disks, pools or a domain; for example:

```sh
python3 000_dusky_vm.py --windows --deploy-only -- --dump-only
```

Start the new VM in virt-manager. In Windows Setup, load `viostor/w11/amd64`
from the VirtIO CD for the virtio disk; use `NetKVM/w11/amd64` if Setup needs
the network driver. Sign in and follow [guest setup](guest/README.md).

## Everyday use

From this `passthrough` directory, as your Wayland desktop user:

```sh
python3 windows/50_win.py launch --vm win11
python3 windows/50_win.py rdp --vm win11 --user YOUR_WINDOWS_USER
```

Use the actual domain name if you chose another. RDP needs a Windows account
password; a Hello PIN is not that password. On first IDD setup, finish sign-in
in the SPICE console, close that viewer, then launch Looking Glass. Opening a
second SPICE viewer can close the Looking Glass client's SPICE connection.

To check a VM and repair known configuration drift:

```sh
python3 windows/50_win.py doctor --vm win11 --guest
python3 windows/50_win.py repair --vm win11 --guest
```

The doctor is read-only; repair requests sudo and rechecks the result. It repairs
managed client options, shared-memory permissions, tmpfiles rules, missing agent
channels and installed guest services. It follows saved setup choices. For an
older domain without choices, it infers LG from IVSHMEM and leaves RDP alone;
use `--access both` or another explicit access choice when appropriate. `--ssh`
adds guest SSH checks. `--json` emits a report; exits are 0 for passing selected
checks, 1 for unresolved issues and 2 for pending checks. Without `--guest`, the
report covers the host only. QGA must already be installed to inspect Windows.

Repair does not download packages, reinstall drivers, bind GPUs, change disks
or reboot. Persistent hardware changes require a guest power-off/start; active
shared memory is never resized or replaced. The canonical mapping supports one
running LG VM at a time. Missing offline inputs and unsupported package versions
are reported for correction. This is an on-demand command, with no background
polling service.

If LG stays at its splash screen after sign-in, focus its window and press a
key or move the pointer to wake the display. If it still has no picture, inspect
the IDD logs described in the guest guide. Passing configuration checks cannot
certify visible pixels, physical audio, or GPU performance.

The [live-test report](guest/LIVE_TEST_NOTES.md) distinguishes exercised behavior
from untested GPU, HDR and physical audio paths.
