---
title: "Remote Desktop — Windows host and FreeRDP on Wayland"
tags:
  - kvm
  - manual
  - windows
reviewed: 2026-10-25
---

# Remote Desktop — Windows host and FreeRDP on Wayland

[[+ MOC KVM|KVM home]] · [[+ MOC Windows Installation Through Virt Manager|Windows route]]

RDP is an optional independent desktop connection, not a requirement for IDD or
permission to disable a recovery display adapter. Windows 11 Pro/Enterprise/
Education can host supported RDP sessions; Home does not supply the equivalent
Remote Desktop host. Check the installed edition first.

## Windows side

In Windows Settings → System → Remote Desktop, enable it and authorize the
intended user. Keep its existing NLA/account policies unless deliberately choosing
a different authentication arrangement. A Windows Hello **PIN is not the
account password** used by ordinary FreeRDP/SSH password authentication.

For the same supported management API used by the bootstrapper, Administrator
PowerShell can enable RDP and its built-in firewall exception:

```powershell
$settings = Get-CimInstance -Namespace 'root/cimv2/TerminalServices' -ClassName Win32_TerminalServiceSetting
$result = Invoke-CimMethod -InputObject $settings -MethodName SetAllowTSConnections -Arguments @{AllowTSConnections=[uint32]1;ModifyFirewallException=[uint32]1}
$result.ReturnValue
Start-Service TermService
```

Require a zero ReturnValue. This uses an API instead of a localized firewall
display-group name. Do not routinely disable NLA, allow blank-password remote
logon or make every network profile Private as a connection fix. Supported blank
local-account scenarios are deliberate exceptions, not the baseline.

## Discover the address and client

From Linux:

```sh
virsh -c qemu:///system domifaddr win11 --source agent
virsh -c qemu:///system net-dhcp-leases default
command -v sdl-freerdp3
sdl-freerdp3 /buildconfig
sdl-freerdp3 /help
```

The installed executable here is `sdl-freerdp3`; package naming can vary. The
launcher accepts documented SDL variants only after confirming
`WITH_CLIENT_SDL3=ON`. Its native Wayland environment is `SDL_VIDEO_DRIVER=wayland`.
The old wlfreerdp/xfreerdp/XWayland fallback list is not this baseline.

A manual example, replacing address/user with your actual guest values:

```sh
SDL_VIDEO_DRIVER=wayland sdl-freerdp3 /v:192.168.122.45 /u:WINDOWS_USER /cert:ignore +dynamic-resolution /network:lan /clipboard /sound
```

The certificate option shown matches the existing local-lab launcher. It is a
connection-policy choice, not necessary to explain Wayland or ballooning.
Let the client request the password rather than storing one in your notes.
The wrapper discovers the VM/address and handles session lifecycle:

```sh
python3 ~/user_scripts/dusky_vm/passthrough/windows/50_win.py rdp --vm win11 --user WINDOWS_USER
```

## Troubleshoot in order

VM running → correct guest IP → service listening on 3389 → routing/firewall →
account/password/authorization → client log. A guest on `default` NAT is normally
reachable from the host; a physical LAN bridge is not required. A Microsoft
account's PIN rejection is not proof the RDP server or GPU is broken.

RDP can change the interactive console state. Disconnect, then sign in through
the local console/Looking Glass when verifying that display path. A successful
RDP picture alone does not prove the same GPU/monitor path is working.

Sources: [Microsoft RDP host setup](https://learn.microsoft.com/en-us/windows-server/remote/remote-desktop-services/remotepc/remote-desktop-allow-access),
[FreeRDP upstream](https://github.com/FreeRDP/FreeRDP).
