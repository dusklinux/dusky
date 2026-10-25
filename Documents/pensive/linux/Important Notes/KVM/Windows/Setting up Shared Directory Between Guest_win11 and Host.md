---
title: "Shared Folder — Virtiofs (Host ↔ Win11)"
tags:
  - kvm
  - windows
  - virtiofs
  - sharing
reviewed: 2026-10-25
---

# Shared Folder — Virtiofs (Host ↔ Win11)

VirtIO-FS exposes a host directory as a Windows filesystem. It works independently
of Looking Glass, SPICE and RDP; no Samba server is needed. Performance depends
on the workload. The tests below establish correctness, not a speed advantage.

Default: **Linux `/mnt/zram1` ↔ Windows `Z:`**. The host folder and Windows drive
letter are configurable. The host folder must exist before the VM starts.
Sharing does not copy files or include them in VM snapshots.

## Windows requirements and scripted equivalent

Prepare the Arch `virtiofsd` package, VirtIO-Win guest tools including `viofs`,
and the official [WinFsp MSI](https://github.com/winfsp/winfsp/releases) for offline
installation. WinFsp's default installation includes its required Core component.

With Windows **powered off**, run as your Linux desktop user:

```sh
cd ~/user_scripts/dusky_vm/passthrough
python3 windows/50_win.py share --vm win11
# Optional different existing host folder:
python3 windows/50_win.py share --vm win11 --dir /path/to/folder
python3 windows/50_win.py share --vm win11 --status
```

Use your actual VM name. The default on first use is `/mnt/zram1`; repeating
`share` retains the selected path and makes no XML change when already configured.
It preserves existing host ownership, permissions and contents. The original XML
is saved under `~/.local/state/dusky/vm-share/DOMAIN_UUID/before-share.xml`.

Start Windows, install VirtIO-Win tools and make sure QEMU guest-agent access
works. Then run on Linux:

```sh
python3 windows/50_win.py share --vm win11 --guest --winfsp-installer 'C:\path\winfsp.msi'
```

The MSI path is **inside Windows**. Once WinFsp is installed, omit that argument:

```sh
python3 windows/50_win.py share --vm win11 --guest
# Optional different unused Windows drive letter:
python3 windows/50_win.py share --vm win11 --guest --drive-letter Y
```

This discovers the Linux caller's UID/GID for guest-created files. It configures
and starts the service and checks the drive appears. The service starts with
Windows. Converged guest setup retains the running service process. Nothing is
downloaded or rebooted automatically. If an installer requests a restart,
restart Windows and rerun `share --guest`.

The helper manages one VirtIO-FS drive and rejects another existing VirtIO-FS
share. Multiple shares require the WinFsp Launcher arrangement documented
upstream; the single VirtioFsSvc service is not a multi-share mount manager.

## Manual host setup

Follow [[09 Shared Folders — VirtIO-FS]] for shared memory, the filesystem XML,
source/tag distinction, permissions and full power-cycle requirements. This is
identical for Windows and Linux guests; the Windows-specific mount is below.
The Windows helper uses tag `dusky-host` and defaults to source `/mnt/zram1`.

## Manual Windows setup

1. Install WinFsp using its defaults/Core component.
2. Install VirtIO-Win guest tools. Check the VirtIO-FS device in Device Manager.
   If unknown or unhealthy, update its driver from the `viofs` directory on the
   VirtIO-Win media, selecting the matching Windows version and architecture.
   An installed tools package alone does not prove every optional service is ready.
3. Copy `windows/guest/setup_share.ps1` into Windows. Run in Administrator
   PowerShell, replacing the owner numbers with Linux `id -u` and `id -g`:

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\setup_share.ps1 -Owner 1000:1000 -DriveLetter Z
```

`1000:1000` is an example, not a required identity. If WinFsp is missing, add
`-WinFspInstaller 'C:\path\winfsp.msi'`.

For fully manual service setup after installing the driver and WinFsp, use
Administrator PowerShell. Replace the owner numbers and choose an unused drive:

```powershell
$binary = Join-Path $env:ProgramFiles 'Virtio-Win\VioFS\virtiofs.exe'
if (-not (Test-Path -LiteralPath $binary)) { throw 'Install VirtIO-FS guest tools first.' }
$service = Get-Service VirtioFsSvc -ErrorAction SilentlyContinue
if ($service) {
    Stop-Service VirtioFsSvc
    (Get-Service VirtioFsSvc).WaitForStatus('Stopped', [TimeSpan]::FromSeconds(30))
} else {
    New-Service -Name VirtioFsSvc -BinaryPathName ('"' + $binary + '"') -StartupType Automatic -DependsOn VirtioFsDrv
}
$key = 'HKLM:\SOFTWARE\VirtIO-FS'
New-Item $key -Force | Out-Null
New-ItemProperty $key -Name MountPoint -Value 'Z:' -PropertyType String -Force | Out-Null
New-ItemProperty $key -Name Owner -Value '1000:1000' -PropertyType String -Force | Out-Null
Set-Service VirtioFsSvc -StartupType Automatic
Start-Service VirtioFsSvc
```

The tag identifies the host filesystem. The drive letter is configured
separately; do not depend on the Explorer volume label matching the tag.
Without an explicit mount point, the service normally chooses the first free
letter starting at Z.

## Troubleshooting

Linux checks, run from the passthrough directory:

```sh
python3 windows/50_win.py share --vm win11 --status
virsh -c qemu:///system dumpxml win11 --inactive
findmnt --target /mnt/zram1
pgrep -a virtiofsd
```

The source directory must exist whenever Windows starts. For a custom directory
under RAM-backed storage, recreate it after host boot. The default shares the
mount root itself. If startup fails, check the directory and virtiofsd package.

Windows checks:

```powershell
Get-Service VirtioFsSvc
Get-PnpDevice -PresentOnly | Where-Object { $_.InstanceId -like 'PCI\VEN_1AF4&DEV_105A*' }
Get-ItemProperty 'HKLM:\SOFTWARE\VirtIO-FS'
Test-Path Z:\
```

A missing drive can indicate missing WinFsp, an unhealthy driver, a stopped
service, a drive-letter collision, or unapplied hardware. It does not establish
a permission problem by itself. Rerun `share --guest` to repair the managed
service configuration. WinFsp MSI failures include a log path under
`%ProgramData%\DuskyVM`.

If the drive appears but writes fail, check the host folder permissions and
Owner UID/GID. Do not recursively chown/chmod an existing mount or its contents
as a generic repair. For a new custom folder, create it as the desktop user and
check access to its parent directories.

Verify both directions with uniquely named test files. Compare transfer hashes
using Linux `sha256sum` and Windows `Get-FileHash -Algorithm SHA256`.

## Verified on 2026-10-25

Tested: virtiofsd 1.14.0, libvirt 12.8, QEMU 11.1.2, WinFsp 2.1.25156,
Windows 11 build 26300. Host kernel was 7.3.0-rc7; this does not certify a final
fresh ISO or other physical hardware. These versions describe a test, not pins.

- `/mnt/zram1` mounted at `Z:`.
- Random 16 MiB transfers in each direction matched SHA-256 hashes.
- Unicode filename creation/rename and Linux user ownership passed.
- 500 guest reads during 500 host atomic replacements passed.
- Host rerun changed no XML; guest rerun retained the service PID.
- Windows restart automatically restored the drive; file hashing passed.
- At the sharing test, all 38 Windows host tests passed, including seven shared-folder tests. Later memory/notes regressions are recorded in [[Verification — Windows VM]].

These checks do not establish a throughput figure or qualify every application
and filesystem operation.

References: [VirtIO-Win service setup/options](https://virtio-win.github.io/Knowledge-Base/Virtiofs-qs.html),
[libvirt VirtIO-FS configuration](https://www.libvirt.org/kbase/virtiofs.html),
[WinFsp releases](https://github.com/winfsp/winfsp/releases).

See also: [[Install a Windows Virtual Machine on KVM]].
