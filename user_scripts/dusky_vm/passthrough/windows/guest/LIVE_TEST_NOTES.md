# Windows 11 live validation — 2026-10-25

Tested against an existing Windows 11 Pro for Workstations VM, build 26300.
The Linux host was not rebooted. Its NVIDIA GPU remained bound to the host;
Looking Glass used the IDD's SDR software rendering path.

| Component | Tested version / configuration |
| --- | --- |
| Looking Glass | Paired official source and IDD artifacts, B7-829 / commit cdba29f2 |
| Linux client | Built with Wayland enabled and X11 disabled; EGL on Intel graphics |
| FreeRDP | Arch 3.32.2, SDL3 client, Wayland |
| Guest Python | Official Python 3.15.1 embeddable runtime |
| Guest tools | VirtIO-Win 0.1.285 media, installed interactively; QGA 110.0.2 |
| SSH test package | Explicit local Win32-OpenSSH 10.4.0.0p1-Preview ZIP |
| Host tooling | libvirt 12.8.0, virt-install 5.1.0, systemd 262.1 |

Observed results:

- Phase 25 configured native IVSHMEM and the VM started successfully with a
  64 MiB region. Windows opened that capacity and reported healthy IVSHMEM,
  Looking Glass display/input devices and the running LGIddHelper service.
- Running Phase 25 again with the active VM retained the same shared-memory
  inode, size and permissions. It did not replace the active mapping.
- The Looking Glass desktop was visually confirmed after console sign-in and
  relaunching the client. Its frame size changed from 1920×1080 to 1264×685,
  matching the client viewport. Logs selected LGMP video, input and clipboard.
- Both clipboard directions passed with distinct test strings. The guest side
  ran in the signed-in user's interactive session; the host clipboard was
  restored afterward.
- The RDP launcher discovered the VM's address and authenticated a temporary
  local account with NLA enabled. A Windows first-sign-in screen was visibly
  rendered in the SDL3 window. Full RDP desktop, audio and clipboard behavior
  were not separately verified.
- Both guest Python PowerShell helpers passed missing-service, early-return
  and genuine terminating-error checks on Windows PowerShell. That initial
  pass included five host configuration tests and three Windows tests.
- The corrected complete bootstrapper returned 0 using the local Python,
  OpenSSH ZIP and publisher certificate. An earlier IDD install returned 3010;
  the requested guest restart was performed and readiness verified afterward.
- SSH public-key authentication to the existing Windows account succeeded.
  The temporary key and RDP test account were removed afterward.
- Deployment XML generated successfully through the script for SPICE,
  SPICE GL and passthrough display modes, with one QGA channel in each.
  All three documents passed the installed libvirt schema validator.

Live failures corrected in the scripts: the client capability probe ran as
root; optional missing-service probes became false PowerShell failures;
legacy autoResize conflicted with dynamic guest resolution; unattended SSH
setup waited for console input; and a local Python override broke the packaged
virt-install's compiled dependencies. The installer now also accepts an
explicit publisher certificate for a first silent installation.

The initial black display was a console handoff: Windows sign-in was on QXL,
the IDD user helper required sign-in, and opening another SPICE viewer closed
the previous Looking Glass client's connection. After sign-in, relaunching
Looking Glass displayed the desktop. See [README.md](README.md) for setup steps.

Limits: no GPU passthrough, HDR, gaming/FPS benchmark, physical audio playback,
or fresh Features-on-Demand OpenSSH installation was tested. The preview ZIP
above was a test input, not a version selected by the scripts; they do not
download it automatically. Use the ISO's chosen offline OpenSSH package/media.
The packaged kernel was 7.2.11, but the running kernel was
`7.3.0-rc7-dusky-battery`; neither establishes the required final 7.3+ baseline.
Packaged Python was 3.14.8; the setup scripts ran under Python 3.15.1. This run does
not certify the final ISO package baseline, which must be checked before release.

## Fresh-install review and follow-up checks

The subsequent review corrected the Windows profile's missing client-config
step, its Linux OS default, and missing IVSHMEM on the ordinary Windows SPICE
path. Both Windows display choices now retain an inbox VGA setup console and
use Looking Glass; the UI no longer offers Linux virgl acceleration for Windows.
Shared-memory setup is checked before creating the VM's disk.

An isolated test showed that `virt-install --print-xml` alone created a disk.
XML generation now also passes `--dry-run`, and deployment's `--dump-only`
skips disk/pool provisioning. Real XML generation and schema validation passed
for both Windows choices, with the target disks remaining absent. No test
domain was registered.

Phase 1 now checks only missing packages. A complete installed package set
does not require a fresh sync database merely to stage local media. Staged,
installed-package and user-supplied VirtIO ISOs are used without an AUR request.
Missing packages still require a consistent package database/package set.

The retained suites passed **15 host tests and 12 Windows tests**. They cover
defaults, preview side effects, offline media, missing inputs, quoted relative
paths, explicit runtime selection, PowerShell errors and SSH restart handling.
Bootstrap lifecycle tests use inert installer fixtures; they do not reinstall
drivers. Features-on-Demand restart results were simulated, not installed from
real FoD media. Interactive UAC elevation was not exercised by these admin tests.

The real RDP management API was exercised successfully through `-EnableRdp`;
the NLA registry value was unchanged. The new explicit publisher-trust helper
accepted the valid official installer and rejected an unsigned fixture. The
official installer and its installed IDD catalog were verified to have the
same signing certificate in this tested build.

The VM was started from its shut-off state for these checks. No Linux host
reboot, GPU rebinding or guest account/password change was performed. A complete
fresh Arch and Windows installation has not been repeated. Use the
[reinstall guide](../README.md) and verify the final ISO package baseline.

## Choice, repair and stress pass

The latest retained suites passed **31 host tests and 14 tests on Windows**.
The latter used Administrator/QGA execution and inert installer fixtures,
including guided console-only setup and unchanged saved choices across reruns.
The host suite includes 5,000 config merges, 100 concurrent atomic config
writes, 1,000 converged XML channel repairs, 100 in-place permission repairs,
allocation failure cleanup, and refusal to resize/recreate an active mapping.
These are convergence and fault tests, not throughput benchmarks.

Live host repair passed ten fault cycles, each followed by a converged rerun
(20 doctor executions). Each cycle injected a legacy conflicting option into a
scratch client config and mode 0600 on the existing live shared-memory file.
Repair restored the config and mode 0660, retained custom options, and preserved
the active inode/capacity. Every converged rerun retained the scratch config's
mtime. Original shared-memory ownership/mode were restored afterward.

Live guest repair also restarted an intentionally stopped SSH service. A
separate isolated saved-choice fixture declined all optional components; repair
skipped their service and RDP changes. The real guest's saved choices were not
modified by that fixture.

All 16 combinations of four access choices, VirtIO/SATA device selection and
GPU/no-GPU display mode produced XML accepted by the installed libvirt schema.
Preview used 1 GiB prospective disks to fit the host's remaining free space;
no disk was created and no test domain was defined. This validates XML shape,
not a GPU assignment or a Windows installation on each device combination.

The signed-in guest reported healthy IDD display/input/IVSHMEM devices,
LGIddHelper, SPICE and RDP. A new LG launch initially showed its splash screen;
forwarding Return was followed by swap-chain assignment and 1264×685 SDR frames.
The actual Windows desktop was then captured and visually verified in LG.
This is consistent with an idle display waking; it is not proof that every
blank display has that cause. Configuration readiness alone is not pixel proof.

Five FreeRDP NLA checks with a temporary account produced explicit
`ERRBASE_SUCCESS` diagnostics; one wrong password produced
`ERRCONNECT_LOGON_FAILURE`. The test account was removed. This SDL3 build
returned exit 1 on successful `+auth-only` runs and 134 on the rejection;
the diagnostic distinction was verified against the supplied FreeRDP source.
Do not use a zero-exit assumption for this optional upstream mode. These tests
did not establish full RDP desktop/audio/clipboard behavior. Windows also logged
Winlogon termination events around the auth-only tests; their wider effect was
not established, and the console desktop remained usable afterward.

The doctor correctly reports the running kernel release candidate as an issue.
Both final integrated host+guest check and repair reports contained 22 checks:
21 passed and only the kernel baseline remained unresolved (exit 1). Repair
retains an installed service's manual/trigger startup configuration unless it
was disabled; disabled selected services are re-enabled before starting them.
No Linux host reboot or GPU rebinding was performed. The tested software paths
have passed the checks above; final ISO, fresh installation, physical GPU, HDR
and physical audio validation remain separate requirements.

Implementation references: the supplied Looking Glass `doc/install_libvirt.rst`,
IDD display/helper code and client config options; FreeRDP's
`client/SDL/SDL3/sdl_context.cpp`, `client/common/cmdline.c` and
`libfreerdp/core/freerdp.c`; the upstream
[current IDD guide source](https://github.com/gnif/LookingGlass/blob/master/doc/install_libvirt.rst)
and Microsoft's documented
[RDP management API](https://learn.microsoft.com/en-us/windows/win32/termserv/win32-terminalservicesetting-setallowtsconnections).
