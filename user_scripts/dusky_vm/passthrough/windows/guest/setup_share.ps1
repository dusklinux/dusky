<# Configure the single VirtIO-FS share after the host has added its device.
   Run as Administrator, or through share_folder.py --guest (QGA runs as SYSTEM).
   Supply a local WinFsp MSI for offline installation. No downloads or reboots.
#>
[CmdletBinding()]
param(
    [string]$WinFspInstaller,
    [ValidatePattern('^[D-Zd-z]$')][string]$DriveLetter = 'Z',
    [ValidatePattern('^[0-9]+:[0-9]+$')][string]$Owner
)
$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'
[Console]::OutputEncoding = [Text.UTF8Encoding]::new($false)
try {
    $admin = ([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
    if (-not $admin) { throw 'Run this script in an Administrator PowerShell terminal.' }
    if (-not $Owner) { throw 'Supply -Owner HOST_UID:HOST_GID; the host share --guest command discovers these automatically.' }
    $winfsp = Get-ItemProperty 'HKLM:\SOFTWARE\WOW6432Node\WinFsp' -ErrorAction SilentlyContinue
    if (-not $winfsp) {
        if (-not $WinFspInstaller -or -not (Test-Path -LiteralPath $WinFspInstaller -PathType Leaf)) {
            throw 'WinFsp is missing. Supply -WinFspInstaller with an offline WinFsp MSI, or install WinFsp Core first.'
        }
        $msi = (Resolve-Path -LiteralPath $WinFspInstaller).ProviderPath
        $logDir = Join-Path $env:ProgramData 'DuskyVM'
        $null = New-Item -ItemType Directory -Path $logDir -Force
        $log = Join-Path $logDir 'winfsp-install.log'
        $p = Start-Process msiexec.exe -ArgumentList @('/i', ('"' + $msi + '"'), '/qn', '/norestart', '/l*v', ('"' + $log + '"')) -Wait -PassThru
        if ($p.ExitCode -eq 3010) { Write-Host 'WinFsp installed; restart Windows and rerun setup.'; exit 3010 }
        if ($p.ExitCode -ne 0) { throw "WinFsp MSI failed ($($p.ExitCode)); see $log." }
        $winfsp = Get-ItemProperty 'HKLM:\SOFTWARE\WOW6432Node\WinFsp' -ErrorAction SilentlyContinue
        if (-not $winfsp) { throw 'WinFsp installation was not detected.' }
    }
    $binary = Join-Path $env:ProgramFiles 'Virtio-Win\VioFS\virtiofs.exe'
    if (-not (Test-Path -LiteralPath $binary -PathType Leaf)) {
        throw 'Install VirtIO-Win guest tools including VirtIO-FS first; virtiofs.exe is missing.'
    }
    $device = @(Get-PnpDevice -PresentOnly | Where-Object { $_.InstanceId -like 'PCI\VEN_1AF4&DEV_105A*' })
    if ($device.Count -ne 1) { throw 'Expected one VirtIO-FS device. Add the host share and power Windows off/start it again.' }
    if ($device[0].Status -ne 'OK') {
        throw 'VirtIO-FS device driver is not ready. Install its viofs driver from the VirtIO-Win media and rerun.'
    }
    $service = Get-CimInstance Win32_Service -Filter "Name='VirtioFsSvc'"
    $drive = $DriveLetter.ToUpperInvariant() + ':'
    $settings = 'HKLM:\SOFTWARE\VirtIO-FS'
    $old = Get-ItemProperty $settings -ErrorAction SilentlyContinue
    $mounted = Get-CimInstance Win32_LogicalDisk -Filter "DeviceID='$drive'"
    if ($mounted -and (-not $service -or $old.MountPoint -ne $drive)) {
        throw "$drive is already in use. Choose a different -DriveLetter."
    }
    $command = '"' + $binary + '"'
    $changed = -not $service -or $service.PathName -ne $command -or $old.MountPoint -ne $drive -or $old.Owner -ne $Owner
    if ($changed -and $service -and $service.State -ne 'Stopped') {
        Stop-Service VirtioFsSvc
        (Get-Service VirtioFsSvc).WaitForStatus('Stopped', [TimeSpan]::FromSeconds(30))
    }
    if (-not $service) {
        New-Service -Name VirtioFsSvc -BinaryPathName $command -StartupType Automatic -DependsOn VirtioFsDrv -DisplayName 'VirtIO-FS Service' | Out-Null
    } elseif ($service.PathName -ne $command) {
        $result = Invoke-CimMethod -InputObject $service -MethodName Change -Arguments @{PathName=$command}
        if ($result.ReturnValue -ne 0) { throw "Updating VirtIO-FS service failed ($($result.ReturnValue))." }
    }
    if ($changed) {
        $null = New-Item $settings -Force
        $null = New-ItemProperty $settings -Name MountPoint -Value $drive -PropertyType String -Force
        $null = New-ItemProperty $settings -Name Owner -Value $Owner -PropertyType String -Force
    }
    Set-Service VirtioFsSvc -StartupType Automatic
    Start-Service VirtioFsSvc
    (Get-Service VirtioFsSvc).WaitForStatus('Running', [TimeSpan]::FromSeconds(30))
    $deadline = [DateTime]::UtcNow.AddSeconds(30)
    while (-not (Test-Path -LiteralPath ($drive + '\'))) {
        if ([DateTime]::UtcNow -ge $deadline) { throw "VirtIO-FS service started but $drive did not appear." }
        Start-Sleep -Milliseconds 250
    }
    Write-Host "VirtIO-FS ready at $drive (host owner $Owner)."
    exit 0
} catch {
    Write-Error -ErrorAction Continue $_
    exit 1
}
