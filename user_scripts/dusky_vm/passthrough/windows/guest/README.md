# Windows 11 guest setup

This setup targets the current Looking Glass **IDD development stack** in the
reference source tree. Stable B7 uses the legacy Host application and is a
different stack. Use the Linux client and Windows IDD installer from the **same
build**; a Git package name alone does not establish that they match.

Download paired components from <https://looking-glass.io/downloads>. Official
source downloads include submodules; a generic GitHub source ZIP may not.
`25_looking_glass.py` accepts a preinstalled current client, or installs
`looking-glass-git` when needed. `--no-packages` requires the client to be
installed already. Keep the shared-memory size large enough for the maximum
guest resolution. New Windows passthrough domains retain VGA/SPICE for setup and
recovery before the IDD is installed.

Copy this guest directory and `looking-glass-idd-setup.exe` into Windows. The
`setup_vdd.py` filename is retained for the bootstrapper, but it now installs the
official IDD, bundled IVSHMEM driver and direct input driver. It does not install
MttVDD or restart the legacy Looking Glass Host service.

For an offline installation, provide:

- Python 3.15+ already installed, a complete runtime at `python\python.exe`, or
  an explicit `-PythonExe PATH`.
- Mounted VirtIO-Win media, `-VirtioIso PATH`, or `virtio-win-guest-tools.exe`
  beside the bootstrapper, for the SPICE guest agent and VirtIO drivers.
- An installed OpenSSH Server, `-OpenSshSource PATH` to Windows
  Features-on-Demand media, or `-OpenSshZip PATH` to an architecture-matching
  Win32-OpenSSH ZIP. Use `-SkipSsh` to omit SSH setup.

Run from PowerShell:

```powershell
.\00_bootstrap.ps1 -PythonExe .\python\python.exe -VirtioIso .\virtio-win.iso -OpenSshSource .\FoD
```

The bootstrapper requests Administrator access when necessary. Use `-SkipSpice`
only when intentionally omitting SPICE clipboard setup. Failures stop the
bootstrapper. Exit code `3010` requests a Windows restart. Rerun the bootstrapper afterward to
finish any remaining steps, then verify the Looking Glass display, direct input
and clipboard before relying on them.

Python install manager also supports offline runtime indexes. Prepare one on a
connected Windows machine with `py install --download=python 3.15`, copy that
whole directory, install Python install manager in the guest, and pass
`-PythonIndex .\python\index.json`. See the [Python offline installation
instructions](https://docs.python.org/3.15/using/windows.html#offline-installs).

SSH setup preserves existing passwords when unattended. An interactive run
allows a password change; Enter keeps the current password. To append a host
public key, run `python .\setup_ssh.py --public-key .\host.pub` after installation.

For RDP, enable Remote Desktop in Windows Settings and use a supported Windows
11 Pro/Enterprise/Education edition; Home cannot host RDP. Authenticate with the
account password, rather than a Windows Hello PIN. For an intentionally blank
local-account password, the launcher accepts `--pass ''`; Windows must already
allow this logon and have NLA disabled. The scripts do not change those policies.
