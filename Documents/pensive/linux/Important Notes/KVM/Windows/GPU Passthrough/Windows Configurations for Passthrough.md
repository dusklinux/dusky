---
title: "Windows Guest — Passthrough Stack (VDD / LG / OpenSSH)"
tags:
  - kvm
  - windows
  - vfio
  - looking-glass
  - vdd
---

# Windows Guest — Passthrough Stack

> [!tip] Workflow
> Do this **inside the Windows VM** after you have a functional NAT or `br0` ([[Network Bridging for LAN access]]). SPICE viewer works until VDD/LG takes over.

## 1. Visual C++ Redistributable

Required for VDD + LG host.

- Download **VC++ latest supported** → Run installer → Reboot if prompted

<https://learn.microsoft.com/en-us/cpp/windows/latest-supported-vc-redist>

## 2. Looking Glass IDD Driver (Modern Stack — Recommended)

The pass-through dGPU has no physical monitor attached. The **Looking Glass IDD Driver** (`looking-glass-idd-setup.exe`) provisions a native kernel-level Indirect Display Driver (`Root\LGIdd`) and direct input device (`Root\LGInput`), while `LGIddHelper` synchronizes frames directly into the IVSHMEM buffer.

> [!tip] Modern IDD vs Legacy MttVDD
> This replaces third-party MttVDD and the old user-space `Looking Glass (host)` background service completely. Do **not** run both; if upgrading from an older B7 setup, disable the old `Looking Glass (host)` service.

### Installation (Silent & Automated via PowerShell as Administrator)

1. Obtain `looking-glass-idd-setup.exe` from the **exact same build** as your host `looking-glass` package.
2. Run in an Administrator PowerShell:

```powershell
# Trust verified installer certificate for silent driver staging:
$sig = Get-AuthenticodeSignature .\looking-glass-idd-setup.exe
if ($sig.Status -eq 'Valid' -and $sig.SignerCertificate) {
    $store = [Security.Cryptography.X509Certificates.X509Store]::new('TrustedPublisher', 'LocalMachine')
    $store.Open('ReadWrite')
    $store.Add($sig.SignerCertificate)
    $store.Close()
    Write-Host "Trusted publisher: $($sig.SignerCertificate.Subject)" -ForegroundColor Green
}

# Stop and disable legacy B7 host service if present:
$old = Get-Service -Name 'Looking Glass (host)' -ErrorAction SilentlyContinue
if ($old) { Stop-Service -InputObject $old -Force; Set-Service -InputObject $old -StartupType Disabled }

# Run silent installer with IVSHMEM driver:
$proc = Start-Process -FilePath .\looking-glass-idd-setup.exe -ArgumentList @('/S', '/ivshmem') -Wait -PassThru
if ($proc.ExitCode -eq 3010) { Write-Host "Restart required to complete driver install." -ForegroundColor Yellow }
```

3. Reboot Windows (`Restart-Computer`).
4. Verify display and input devices in PowerShell:
   ```powershell
   Get-PnpDevice -Class Display,HIDClass -PresentOnly | Where-Object { $_.InstanceId -match 'Root\\LGIdd|Root\\LGInput' }
   Get-Service LGIddHelper
   ```
   Status must be `OK` and service `Running`.

### Legacy Alternative — Third-Party MttVDD + LG Host (Historical B7 Only)

> [!warning] Legacy Path
> Only use this if running older Looking Glass B7 stable releases without IDD support.
> - **Repo:** <https://github.com/VirtualDrivers/Virtual-Display-Driver>
> - Extract to `C:\VirtualDisplayDriver` → import certificate to `TrustedPublisher` and `Root` → install via `devcon install ... VirtualDisplayDriver.inf Root\MttVDD`.
> - Install legacy Looking Glass Host to `C:\Program Files\Looking Glass (host)\looking-glass-host.exe` and configure autostart.
> - Configure `C:\VirtualDisplayDriver\vdd_settings.xml` for 1440p SDR.

## 3. NVIDIA Guest Driver

Standard GeForce driver for your card inside VM → install → reboot. Do **not** disable the `NVIDIA Display` adapter nor the `Looking Glass IDD` display device in Device Manager.

## 4. Utilities

- **7-Zip** — <https://www.7-zip.org>
- **O&O ShutUp10++** — <https://www.oo-software.com/en/download/current/ooshutup10>
- **Windows Update MiniTool** — <https://www.majorgeeks.com/files/details/windows_update_minitool.html>
- **WinFsp** — <https://github.com/winfsp/winfsp> (for virtiofs folder sharing)

## 5. OpenSSH Server (Offline or Portable)

Online `DISM /Add-Capability` often hangs on fresh VMs due to Windows Update service locks. Use offline Features-on-Demand or portable Win32-OpenSSH:

### Option A: Offline Features-on-Demand (Windows Feature)

```powershell
# Point -Source to mounted Windows FoD / ISO media directory
Add-WindowsCapability -Online -Name OpenSSH.Server~~~~0.0.1.0 -Source "D:\LanguagesAndOptionalFeatures" -LimitAccess
Set-Service sshd -StartupType Automatic; Start-Service sshd
```

### Option B: Win32-OpenSSH Portable Package

```powershell
# 1. Download OpenSSH-Win64.zip (or copy from host share)
# 2. Expand to C:\Program Files\OpenSSH-Win64
powershell -ExecutionPolicy Bypass -File "C:\Program Files\OpenSSH-Win64\install-sshd.ps1"
Set-Service sshd -StartupType Automatic; Start-Service sshd
```

### Firewall & Key Permissions (Both Options)

```powershell
# Firewall rule
New-NetFirewallRule -Name "SSH" -DisplayName "OpenSSH SSH Server" -Enabled True -Direction Inbound -Protocol TCP -Action Allow -LocalPort 22 2>$null
# Default shell to PowerShell:
New-ItemProperty -Path "HKLM:\SOFTWARE\OpenSSH" -Name "DefaultShell" -Value "C:\Windows\System32\WindowsPowerShell\v1.0\powershell.exe" -PropertyType String -Force

# Optional: authorized_keys setup for passwordless SSH:
$authKeys = "$env:ProgramData\ssh\administrators_authorized_keys"
# Append host public key:
Add-Content -Path $authKeys -Value "ssh-ed25519 AAAAC3... operator@host"
# Mandatory strict Windows OpenSSH ACLs on administrator key:
icacls.exe $authKeys /inheritance:r /grant:r "*S-1-5-18:(F)" "*S-1-5-32-544:(F)"
```

## 7. Quirks

- **VirtIO-FS Service** appears only after `virtio-win-guest-tools` installed.
- **Cursor vanishing** after `vioinput` → Device Manager → uninstall mouse → reinstall while viewing via Looking Glass (or uninstall `vioinput` via x64 pkg then re-add).

Next: [[Looking Glass]] (host `$XDG` sharing `/dev/shm/looking-glass` wire-up).
