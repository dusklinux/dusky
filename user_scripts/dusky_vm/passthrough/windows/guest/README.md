# Windows 11 guest setup

Start with the [Arch reinstall/run-order guide](../README.md) for the host.

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

Deployment runs the packaged `virt-install` with system executable paths first,
so a local `python3` override does not mix its interpreter with another Python
version's compiled bindings. The setup scripts themselves require Python 3.15+;
the final ISO must ship a matching set of Python and distribution bindings.

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

For guided setup, run `powershell.exe -NoProfile -ExecutionPolicy Bypass -File
.\00_bootstrap.ps1` without arguments. It asks separately about Looking Glass,
RDP, SSH and guest tools, then requests elevation once. Use `-Interactive` when
also supplying paths or other explicit options. Explicit switches are respected
without asking again. For automation, provide the desired switches and paths:

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\00_bootstrap.ps1 -PythonExe .\python\python.exe -SkipSsh -TrustIddPublisher -EnableRdp
```

The process-only execution policy above allows the local bootstrapper to start
on fresh Windows without changing the machine's execution policy.
This example sets up Looking Glass and RDP without SSH, using mounted VirtIO-Win
media. To include SSH, replace `-SkipSsh` with `-OpenSshZip .\OpenSSH-Win64.zip`
or `-OpenSshSource .\FoD`. Use `-VirtioIso PATH` if the driver ISO is a local
file instead of an already mounted DVD.
Use `-SkipLookingGlass` for RDP/console-only guests. Python is needed only when
installing LG or SSH. Choices are saved atomically in
`%ProgramData%\DuskyVM\choices.json`; the host doctor follows them when repairing
guest components. Declining a component leaves any existing installation alone;
it does not uninstall or disable a previously configured component.
The bootstrapper requests Administrator access when necessary. Use `-SkipSpice`
only when intentionally omitting SPICE clipboard setup. Failures stop the
bootstrapper. Exit code `3010` requests a Windows restart. Rerun the bootstrapper afterward to
finish any remaining steps, then verify the Looking Glass display, direct input
and clipboard before relying on them.

For a first silent IDD installation, Windows must trust the driver publisher.
`-TrustIddPublisher` explicitly imports the installer's signing certificate into
Trusted Publishers only after Windows reports a valid Authenticode signature.
It prints the verified publisher. The tested official installer and driver
catalog used the same certificate; no thumbprint or issuer is pinned in code.
Standalone use is `setup_vdd.py --trust-installer-publisher`.

Alternatively, provide a prepared certificate with `-IddPublisherCertificate .\publisher.cer`
(`setup_vdd.py --publisher-certificate .\publisher.cer` when used directly).
This imports that supplied certificate into the local machine's Trusted
Publishers store. It does not modify the root certificate store or enable test
signing. Prepare the certificate with the paired IDD files before an offline
installation. You can export it directly from the signed installer, without
first running an installation:

```powershell
$signature = Get-AuthenticodeSignature .\looking-glass-idd-setup.exe
$signature | Format-List Status,StatusMessage,SignerCertificate
# Verify the expected publisher and a Valid signature before exporting.
if ($signature.Status -ne 'Valid') { throw 'Resolve the certificate chain before continuing.' }
Export-Certificate -Cert $signature.SignerCertificate -FilePath .\publisher.cer
```

For a completely offline guest, its Windows image must also have the required
CA chain. A publisher certificate alone does not repair a missing root or
intermediate. Check signature validation on the Windows image used for offline
deployment during media preparation; the script reports the Windows validation
error and stops if the signature cannot be verified.

An unknown publisher can cause a silent installer to exit with code 1. Its
driver log is `%SystemRoot%\inf\setupapi.dev.log`; rerun with the supplied
publisher certificate after resolving that cause. Microsoft documents this
[requirement for silent driver installation](https://learn.microsoft.com/en-us/windows-hardware/drivers/install/trusted-publishers-certificate-store).

For initial setup, sign in through the VGA/QXL SPICE console. With both
monitors present, Windows can show the sign-in screen only on the original
display. The IDD's user helper starts after sign-in and, by default, makes
Looking Glass the only active monitor. The SPICE console can then go black.
Close that viewer and launch `50_win.py view --vm VM_NAME` to use Looking Glass.
Opening another SPICE viewer can disconnect Looking Glass's SPICE connection
and close its client; relaunch Looking Glass after finishing console access.
A connected client and valid frame metadata alone do not prove that a usable
desktop is visible.
An idle display can also leave LG at its splash screen until input wakes it;
focus LG and press a key or move the pointer before replacing any drivers.
IDD driver/service logs are in `%ProgramData%\Looking Glass (IDD)`, and the
interactive helper log is in `%LocalAppData%\Looking Glass (IDD)`.

The bootstrapper checks explicit paths and required guest files before starting
installers. It resolves relative input paths before elevation. A mistyped
`-PythonExe` is reported instead of silently selecting another runtime. SSH
Features-on-Demand restart requests also stop setup with exit code 3010.

`60_configure_client_ini.py` selects `autoResize=no` and `setGuestRes=yes`:
Windows follows the client window size. Enabling both options prevents the
current client from starting.

Python install manager also supports offline runtime indexes. Prepare one on a
connected Windows machine with `py install --download=python 3.15`, copy that
whole directory, install Python install manager in the guest, and pass
`-PythonIndex .\python\index.json`. See the [Python offline installation
instructions](https://docs.python.org/3.15/using/windows.html#offline-installs).

The bootstrapper preserves existing passwords and never prompts to change one.
A direct interactive `setup_ssh.py` run
allows a password change; Enter keeps the current password. To append a host
public key, run `python .\setup_ssh.py --public-key .\host.pub` after installation.
Pass `--non-interactive` for automation, including guest-agent commands: a
Windows process can still have a console despite being launched unattended.

For RDP, enable Remote Desktop in Windows Settings and use a supported Windows
11 Pro/Enterprise/Education edition; Home cannot host RDP. Authenticate with the
account password, rather than a Windows Hello PIN. For an intentionally blank
local-account password, the launcher accepts `--pass ''`; Windows must already
allow this logon and have NLA disabled. The scripts do not change those policies.
`-EnableRdp` enables the supported Windows Remote Desktop host and its firewall
exception through Windows' management API. It preserves the existing NLA policy
and account passwords. Omit it to leave the Remote Desktop setting alone.

## Initial access with the QEMU guest agent

New domains created by `deploy_vm.py` include both SPICE and QEMU guest-agent
channels. For an existing VM, add this inside `<devices>` in its libvirt XML:

```xml
<channel type='unix'>
  <target type='virtio' name='org.qemu.guest_agent.0'/>
</channel>
```

Mount the VirtIO-Win ISO in the VM, then open **This PC → VirtIO-Win DVD** and run
`virtio-win-guest-tools.exe` as Administrator with its default components.
Power the guest off and start it again if its device configuration changed;
an in-guest restart does not apply new persistent libvirt hardware. After the
drivers and service start, check from Linux:

```sh
virsh -c qemu:///system qemu-agent-command VM_NAME '{"execute":"guest-ping"}'
```

A successful reply is `{"return":{}}`. This provides management access without
the Microsoft account password; the Windows Hello PIN is not an SSH/RDP password.
For RDP, enable **Settings → System → Remote Desktop**, retain NLA for a
password-based account, and allow the built-in Remote Desktop firewall rules.

A guest power cycle preserves a disk stored in host tmpfs. A host reboot does
not. `backup_restore_virt_xml_config.py` backs up configuration, not VM disks:
copy the powered-off disk to persistent storage separately before rebooting
the host.

See [LIVE_TEST_NOTES.md](LIVE_TEST_NOTES.md) for exercised versions, results and
remaining validation limits.

The regression checks do not install or change services. From the host's
`windows` directory, run `python3 -m unittest discover -s tests -v`. Inside the
copied guest directory, run `python -m unittest discover -s .\tests -v`.
Bootstrap fixture tests require an Administrator terminal to avoid interactive
UAC prompts; other helper tests do not need elevation.
