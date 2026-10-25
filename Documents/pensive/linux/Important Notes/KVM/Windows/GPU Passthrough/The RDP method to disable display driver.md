---
title: "RDP Rescue — Disable Emulated Display Adapter"
tags:
  - kvm
  - vfio
  - rdp
  - arch
---

# RDP Rescue — Disable Emulated Display Adapter

> [!abstract] Goal
> Safely hand off rendering from `QXL`/`Microsoft Basic` (emulated) to pass-through NVIDIA via RDP, keeping a live session after the emulated adapter goes dark ([[Looking Glass]] Phase 3.2). Canonical automation: `55_rdp.py` (+ `50_win.py` dispatch).

## Prereqs

- [ ] Custom ISOs: confirm RDP **not** ripped out (your Win10 lite had RDP removed — use Win11 or non-stripped image for this path; else use virt-manager method in [[Looking Glass_old]])
- [ ] Network virtio: [[Configure Virtual Network Interface]] → `virtio` + **Option 1/3** bridge/NAT per [[Network Bridging for LAN access]]
- [ ] IP known, user with password, RDP enabled, network **Private**

### 1. IP

Inside VM guest (Task Manager → Performance → Wi-Fi/Ethernet, or PowerShell):

```powershell
ipconfig   # IPv4, e.g. 192.168.122.9
Get-NetIPAddress -AddressFamily IPv4 | Where-Object IPAddress -like "192.168.*"
```

From host (lease view): `virsh -c qemu:///system domifaddr <vm> --source lease` or `virsh net-dhcp-leases default`

### 2. User + password

Set a password (blank requires GPO to disable limit):

```powershell
# PowerShell as Admin → set password for current user via GUI: Settings → Accounts → Sign-in options
# or enable blank-password remote logon:
secpol.msc → Local Policies → Security Options → "Accounts: Limit local account use of blank passwords …" → Disabled
# Home edition (no secpol): reg add "HKLM\SYSTEM\CurrentControlSet\Control\Lsa" /v LimitBlankPasswordUse /t REG_DWORD /d 0 /f
```

### 3. Enable RDP

```powershell
Settings → System → Remote Desktop → Enable (On)
# or PowerShell:
Set-ItemProperty -Path 'HKLM:\System\CurrentControlSet\Control\Terminal Server' -Name fDenyTSConnections -Value 0
Enable-NetFirewallRule -DisplayGroup "Remote Desktop"
```

### 4. Network Private (persistent)

```powershell
Get-NetConnectionProfile   # likely Public
Set-NetConnectionProfile -NetworkCategory Private   # temporary
# persist across reboots: secpol.msc → Network List Manager Policies → Unidentified Networks → Location type = Private
```

## Hand-off (the rescue)

> [!warning] Never disable the adapter inside virt-manager viewer
> You lose mouse + video with no live channel back. **Always via RDP**.

```bash
# from Arch host (55_rdp.py probes MAC → leases → ARP; tests port 3389 reachability)
# Wayland native (FreeRDP 3.x):
wlfreerdp /v:192.168.122.9 /u:dusky /cert:ignore /gfx:AVC444 /network:auto +clipboard /dynamic-resolution
# Or xfreerdp3 / sdlfreerdp3 fallback:
xfreerdp3 /v:192.168.122.9 /u:dusky /cert:ignore /dynamic-resolution
# cached in ~/.config/dusky/settings/virt/win_state (rdp_ip/rdp_user)
```

**Inside RDP:**

1. `devmgmt.msc` → **Display adapters**
2. Right-click **Microsoft Basic Display Adapter** / **Red Hat QXL controller** → **Disable device → Yes**
   Windows loses emulated output → scans next GPU → **NVIDIA** wakes; RDP stays (independent RDP channel)
3. Continue in [[Looking Glass]] Phase 3 (Looking Glass IDD driver setup, `Root\LGIdd` + `Root\LGInput`, and IVSHMEM capture via `LGIddHelper`).

> [!tip] Troubleshoot
> `55_rdp.py:print_rdp_troubleshooting` checks WARP/VPN/UFW (host) and gives one-click PowerShell to: `fDenyTSConnections=0`, `UserAuthentication=0`, `LimitBlankPasswordUse=0`, `NetworkCategory Private`, `Enable-NetFirewallRule -DisplayGroup "Remote Desktop"`. Also verifies `xfreerdp3` binary (`freerdp` pkg).

See: `55_rdp.py`, `50_win.py` (`rdp`/`launch` actions, SPICE wait), [[Looking Glass]].
