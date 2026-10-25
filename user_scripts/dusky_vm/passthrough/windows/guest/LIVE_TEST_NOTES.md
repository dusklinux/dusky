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
  and genuine terminating-error checks on Windows PowerShell. The retained
  regression suite has five host configuration tests and three Windows tests
  (each Windows test checks both helpers).
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
The development host had kernel 7.2.11 and packaged Python 3.14.8, below the
planned ISO floors; the setup scripts ran under Python 3.15.1. This run does
not certify the final ISO package baseline, which must be checked before release.
