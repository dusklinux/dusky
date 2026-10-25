---
title: "Windows OpenSSH Server — optional shell and file access"
tags:
  - kvm
  - manual
  - windows
reviewed: 2026-10-25
---

# Windows OpenSSH Server — optional shell and file access

[[+ MOC KVM|KVM home]] · [[+ MOC Windows Installation Through Virt Manager|Windows route]]

SSH is optional. QGA can manage a guest without an account password, and Looking
Glass/RDP do not depend on sshd. For SSH, distinguish the Windows Server feature
from a portable Win32-OpenSSH distribution. Choose one installed server rather
than layering two services with the same name.

## Windows feature — offline source

In Administrator PowerShell, inspect current capability/service state. If absent,
supply Features-on-Demand media matching the Windows build/architecture:

```powershell
Get-WindowsCapability -Online -Name OpenSSH.Server~~~~0.0.1.0
Add-WindowsCapability -Online -Name OpenSSH.Server~~~~0.0.1.0 -Source 'D:\LanguagesAndOptionalFeatures' -LimitAccess
```

Replace the source with the actual media location. A generic Windows installer
DVD is not guaranteed to include the required FoD payload. If RestartNeeded is
true, restart Windows and finish afterward. Omitting Source/LimitAccess is an
online Windows Update route, not an offline fallback.

## Portable alternative

Prepare the architecture-matching ZIP from
[Win32-OpenSSH releases](https://github.com/PowerShell/Win32-OpenSSH/releases).
Expand its complete directory to Program Files, then run its included
`install-sshd.ps1` as Administrator according to that release's instructions.
For an x64 archive extracted to the directory shown, Administrator PowerShell
can invoke it as follows; adapt the path to the actual extracted layout:

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File 'C:\Program Files\OpenSSH-Win64\install-sshd.ps1'
```

The helper `windows/guest/setup_ssh.py --openssh-zip PATH --non-interactive`
handles this route; `--source PATH` handles FoD instead.

After either supported installation:

```powershell
Set-Service sshd -StartupType Automatic
Start-Service sshd
Get-Service sshd
# The feature may have created this rule already:
Get-NetFirewallRule -Name 'OpenSSH-Server-In-TCP' -ErrorAction SilentlyContinue
```

If the intended server has no such rule, Administrator PowerShell can add it;
an existing matching rule is enabled without replacing its other settings,
matching `setup_ssh.py`:

```powershell
$ErrorActionPreference = 'Stop'
if (-not (Get-NetFirewallRule -Name 'OpenSSH-Server-In-TCP' -ErrorAction SilentlyContinue)) {
    New-NetFirewallRule -Name 'OpenSSH-Server-In-TCP' -DisplayName 'OpenSSH Server (sshd)' -Enabled True -Direction Inbound -Protocol TCP -Action Allow -LocalPort 22
} else {
    Enable-NetFirewallRule -Name 'OpenSSH-Server-In-TCP'
}
```

The setup helper uses that stable rule name. A missing service, capability
download error, firewall block and account authentication error are different
failures. `Get-NetTCPConnection -LocalPort 22 -State Listen` checks the listener;
it does not prove that an account can authenticate.

## Public keys and account identity

Use a normal Windows account password when password authentication is chosen;
a Hello PIN is not that password. For keys, transfer the Linux **public** key.
Standard accounts normally use their profile's `.ssh\authorized_keys`.
The default Windows sshd admin-group Match configuration instead uses
`%ProgramData%\ssh\administrators_authorized_keys`; inspect the effective config.
Administrator key files require the documented SYSTEM/Administrators ACLs.
The helper's `--public-key PATH` appends that administrator key and applies those
ACLs without saving/changing passwords.

For a manual **administrator-account** key setup, copy the host public key into
Windows, then use Administrator PowerShell. This preserves existing key lines,
does not append a duplicate, and writes UTF-8 without a BOM:

```powershell
$ErrorActionPreference = 'Stop'
$publicKey = (Get-Content -LiteralPath 'C:\path\host.pub' -Raw).Trim()
$authorized = Join-Path $env:ProgramData 'ssh\administrators_authorized_keys'
$null = New-Item -ItemType Directory -Path (Split-Path $authorized) -Force
$lines = @()
if (Test-Path -LiteralPath $authorized) { $lines = @([IO.File]::ReadAllLines($authorized)) }
if ($publicKey -cnotin $lines) { $lines += $publicKey }
[IO.File]::WriteAllLines($authorized, [string[]]$lines, [Text.UTF8Encoding]::new($false))
icacls.exe $authorized /inheritance:r /grant:r '*S-1-5-18:(F)' '*S-1-5-32-544:(F)'
if ($LASTEXITCODE -ne 0) { throw 'Could not apply the administrator key-file ACL.' }
icacls.exe $authorized
```

The SIDs identify SYSTEM and the local Administrators group independently of
display language. Inspect the final ACL; these commands replace those two
principals' grants and remove inherited entries, but do not remove unrelated
pre-existing explicit grants. Resolve such entries against the documented
Windows OpenSSH key-file requirements if authentication rejects the file.
For a standard account, use that account's own `.ssh\authorized_keys` and its
corresponding user permissions instead; do not put every user's key in the
administrator file. A password-only setup does not require creating key files.

From Linux, discover the guest address and connect:

```sh
ssh WINDOWS_USER@GUEST_ADDRESS
scp /path/to/local-file WINDOWS_USER@GUEST_ADDRESS:.
```

Replace the placeholders. Successful SSH/SCP proves that account's command/file
path, not a Windows desktop login or a Looking Glass picture. For logs inspect
OpenSSH/Operational in Event Viewer and the actual sshd configuration.

Source: [Microsoft Windows OpenSSH configuration](https://learn.microsoft.com/en-us/windows-server/administration/openssh/openssh-server-configuration).
