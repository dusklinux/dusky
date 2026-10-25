---
title: "Looking Glass — paired IDD and native Wayland client"
tags:
  - kvm
  - manual
  - windows
reviewed: 2026-10-25
---

# Looking Glass — paired IDD and native Wayland client

[[+ MOC KVM|KVM home]] · [[+ MOC Windows Installation Through Virt Manager|Windows route]] · [[+ MOC Windows GPU Passthrough|GPU route]]

This guide describes the **paired IDD development stack used by these scripts**.
Stable B7's legacy capture Host is a different implementation. Do not combine
a development IDD installer with an arbitrary stable client/package. Obtain both
from the same tested build/release: [Looking Glass downloads](https://looking-glass.io/downloads).

## What each part does

Windows renders to an IDD virtual display. Its driver/helper transports frames
through IVSHMEM to the Linux client, which presents them on Wayland. Direct input
and supported clipboard traffic have their own IDD paths; SPICE can supply
fallback display/clipboard/audio. QGA remains a separate management channel.
The helper has interactive-session behavior, so “the service is running” is not
proof a signed-in desktop is being captured.

The IDD can use a suitable Windows GPU or software processing. A passed-through
GPU is recommended for acceleration, but not required for a setup picture.
Software mode has limits, including SDR; it is not a high-refresh performance
qualification. No full-path “zero-copy” guarantee follows from a shared-memory
file. The regular-file backend here is distinct from optional KVMFR DMA support;
KVMFR/kernel headers are not prerequisites for this chosen ivshmem-plain setup.

## 1. Host client and shared-memory size

Install/build the **matching** Linux client with Wayland enabled. The scripts
accept a suitable preinstalled current client; generic package names alone do
not establish a match. Official source archives include needed submodules;
plain repository ZIPs can omit them. Prepare source/build inputs before going
offline. This baseline adds no Xorg/XWayland client fallback.

Use the paired IDD's sizing rule, not the old double-buffer-plus-10-MiB formula:

```text
stride = ceil(width × 4 / 256) × 256
bytes = stride × height × 3 + 4 MiB
region = next power of two at least bytes
```

| Maximum mode | Region |
|---|---|
| 1920×1080 | 32 MiB |
| 2560×1440 | 64 MiB |
| 3840×2160 | 128 MiB |

Current IDD HDR transport uses the same 32-bit allocation; compositor/display
HDR presentation has separate requirements and was not qualified in the live
test. Insufficient shared memory can filter/reject modes; oversizing beyond
needed modes only reserves additional host RAM.

## 2. Native libvirt shared-memory device

With the guest powered off and all users of the mapping stopped, merge inside
`<devices>`:

```xml
<shmem name='looking-glass'>
  <model type='ivshmem-plain'/>
  <size unit='M'>64</size>
</shmem>
```

Use a different power-of-two size when required above. The file is
`/dev/shm/looking-glass`. Avoid a second raw `qemu:commandline` device for the same
mapping. Keep the emulated VGA/SPICE setup console and both agent channels.
A `memoryBacking memfd shared` block is for VirtIO-FS, not required merely
because this separate IVSHMEM device exists.

For manual first-use file staging, choose the actual desktop user and kvm group.
For the 64 MiB example:

```sh
vm_lg_user=$(id -un)
printf 'f /dev/shm/looking-glass 0660 %s kvm - -\n' "$vm_lg_user" | sudo tee /etc/tmpfiles.d/10-looking-glass.conf
sudo systemd-tmpfiles --create /etc/tmpfiles.d/10-looking-glass.conf
# As the file's desktop owner, only while no guest/client is using it:
fallocate -l 64M /dev/shm/looking-glass
stat /dev/shm/looking-glass
```

`f` creates a missing file without truncating an existing one. Check its current
size/ownership first; do not delete/recreate/resize a live mapping to repair a
permission error. Failed allocation is a real failure, not permission to claim
success with a sparse fallback. The script uses posix_fallocate and refuses an
active replacement. Verify that libvirt/QEMU can also access the file.
Only one active VM should use this canonical mapping at a time.

The scripted equivalent, run as the desktop user, is:

```sh
python3 ~/user_scripts/dusky_vm/passthrough/windows/25_looking_glass.py --domain win11 --shm-mib 64 --no-packages
```

It requests elevation and may offer controlled domain shutdown for hardware
changes. `--no-packages` assumes the matching client is already installed.
Fixed RAM is the default, but an explicitly configured balloon device is now
preserved: [[07 Memory — Fixed and Ballooning]].

## 3. Guest installation — use the setup console first

Install VirtIO-Win guest tools, then the appropriate current GPU vendor driver
if passing a card through. Confirm the physical GPU is healthy before judging
acceleration. Keep VGA/SPICE for setup and recovery.

Run the **matching** `looking-glass-idd-setup.exe` as Administrator and select IDD,
direct input and the bundled IVSHMEM driver as needed. The installer can disable
the legacy Looking Glass Host; only one server should own the same mapping.
No MttVDD, devcon or vdd_settings.xml is part of this IDD route.

For manual silent installation, Windows must trust the driver publisher.
A valid installer signature alone does not automatically add that publisher.
Use interactive installation if you want Windows to handle the prompts. For
an explicit silent-install trust step, inspect the signature/publisher first in
Administrator PowerShell:

```powershell
$signature = Get-AuthenticodeSignature .\looking-glass-idd-setup.exe
$signature | Format-List Status,StatusMessage,SignerCertificate
if ($signature.Status -ne 'Valid' -or -not $signature.SignerCertificate) {
    throw 'Resolve the signature/chain failure before staging this publisher.'
}
# After checking that this is the intended paired installer's publisher:
$store = [Security.Cryptography.X509Certificates.X509Store]::new('TrustedPublisher','LocalMachine')
$store.Open('ReadWrite')
try { $store.Add($signature.SignerCertificate) } finally { $store.Close() }
```

This does not import a root CA or enable test signing. Offline Windows must
already have the required CA chain; adding a publisher does not repair a missing
root/intermediate. The helper's explicit trust option follows this same process.
With the publisher ready:

```powershell
$process = Start-Process .\looking-glass-idd-setup.exe -ArgumentList @('/S','/ivshmem') -Wait -PassThru
if ($process.ExitCode -eq 3010) {
    Write-Host 'Restart Windows when ready, then finish verification.'
} elseif ($process.ExitCode -ne 0) {
    throw "IDD installation failed with exit code $($process.ExitCode)."
}
```

`/S` is uppercase. Restart Windows when requested (including exit 3010), then
verify the actual devices and service:

```powershell
Get-PnpDevice -PresentOnly | Where-Object { $_.InstanceId -match 'LGIdd|LGInput|IVSHMEM' }
Get-Service LGIddHelper
```

Sign in normally. The interactive helper defaults to making LG the only active
monitor. The old SPICE console can therefore become black while Looking Glass
works. This does not require manually disabling the emulated adapter in Device
Manager. If multiple monitors are intentional, adjust the helper's monitor
preference and Windows display arrangement instead of removing the recovery GPU.

## 4. Linux client configuration and launch

Minimal `~/.config/looking-glass/client.ini`:

```ini
[lgmp]
shmDevice=/dev/shm/looking-glass
[win]
autoResize=no
setGuestRes=yes
[input]
escapeKey=KEY_F6
[spice]
enable=yes
clipboard=yes
```

Do not enable both autoResize and setGuestRes; the tested client rejects that
combination. `shmDevice` belongs in `[lgmp]`, not an invented `[app]` key.
`KEY_F6` is the supported symbolic key name. Other rendering/audio/scaling options
are preferences to verify against that client, not mandatory optimization flags.

Use the manager to discover this domain's active SPICE endpoint:

```sh
python3 ~/user_scripts/dusky_vm/passthrough/windows/50_win.py launch --vm win11
```

For a manual launch, inspect `virsh -c qemu:///system domdisplay win11 --type spice`
and the active XML. Substitute the **actual** port below; do not assume 5900 for
multiple running VMs:

```sh
looking-glass-client app:transport=lgmp -f /dev/shm/looking-glass -m KEY_F6 spice:host=127.0.0.1 spice:port=ACTUAL_PORT
```

Run from the desktop Wayland session as the desktop user. The manager also
handles Unix SPICE sockets/no-SPICE domains. Opening another SPICE viewer can
disconnect Looking Glass's owned SPICE connection; relaunch after console work.

## 5. Diagnose by layer

1. Client won't start: inspect its error/config and matching build, then mapping
   access/size. Do not unlink a live file.
2. Splash/black picture: sign in through the usable console/display, wake an idle
   display with input, then inspect IDD mode/active-monitor arrangement.
3. No hardware acceleration: check Windows driver/PnP health and IDD-selected
   adapter. A visible software desktop is not proof the physical GPU works.
4. Clipboard/input: test each direction in simple text editors; distinguish IDD,
   SPICE and RDP sessions. A QGA ping does not validate clipboard.
5. Faults/corruption: collect logs and the paired build/version before changing
   copy-mode workarounds. Do not force slower indirect copies as a universal fix.

Logs: `%ProgramData%\Looking Glass (IDD)` for driver/service and
`%LocalAppData%\Looking Glass (IDD)` for the interactive helper. Check the actual
client help for key bindings instead of copying an old numeric-key hotkey table.
See [[Verification — Windows VM]] for observed pixels/rendering tests and limits.

Build-specific references: preserved `doc/install_idd.rst`, `install_libvirt.rst`,
`idd_configuration.rst` and `idd_diagnostics.rst` in the paired source tree;
[upstream Looking Glass documentation](https://looking-glass.io/docs/).
