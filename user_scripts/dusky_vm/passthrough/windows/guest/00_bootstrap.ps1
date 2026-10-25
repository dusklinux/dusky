<#
.SYNOPSIS
    Configure Windows 11 guest tools and the matching Looking Glass IDD.
.DESCRIPTION
    Uses Python 3.15+ already installed or bundled at python\python.exe.
    PythonIndex may reference an offline index produced by Python install manager.
    Place looking-glass-idd-setup.exe beside this script, from the Linux client's build.
#>
[CmdletBinding()]
param(
    [string]$PythonExe,
    [string]$PythonIndex,
    [string]$IddInstaller,
    [string]$IddPublisherCertificate,
    [string]$VirtioIso,
    [string]$OpenSshSource,
    [string]$OpenSshZip,
    [switch]$SkipSsh,
    [switch]$SkipSpice
)
$ErrorActionPreference = 'Stop'
$scriptDir = $PSScriptRoot

function Quote-PS([string]$Value) { "'" + $Value.Replace("'", "''") + "'" }

$isAdmin = ([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
if (-not $isAdmin) {
    # An encoded invocation preserves paths, switches and the child's exit code.
    $invocation = '& ' + (Quote-PS $PSCommandPath)
    foreach ($item in $PSBoundParameters.GetEnumerator()) {
        $invocation += ' -' + $item.Key
        if ($item.Value -is [System.Management.Automation.SwitchParameter]) {
            $invocation += ':$' + $item.Value.IsPresent.ToString().ToLowerInvariant()
        } else { $invocation += ' ' + (Quote-PS ([string]$item.Value)) }
    }
    $encoded = [Convert]::ToBase64String([Text.Encoding]::Unicode.GetBytes($invocation))
    $child = Start-Process powershell.exe -ArgumentList @('-NoProfile', '-ExecutionPolicy', 'Bypass', '-EncodedCommand', $encoded) -WorkingDirectory (Get-Location).Path -Verb RunAs -Wait -PassThru
    exit $child.ExitCode
}

function Find-Python {
    # A version probe must never trigger Python manager's automatic online install.
    $env:PYTHON_MANAGER_AUTOMATIC_INSTALL = 'false'
    $candidates = @($PythonExe, (Join-Path $scriptDir 'python\python.exe'))
    $command = Get-Command python.exe -ErrorAction SilentlyContinue
    if ($command) { $candidates += $command.Source }
    $probe = 'import sys; print(sys.executable); sys.exit(0 if sys.version_info >= (3,15) else 1)'
    foreach ($candidate in $candidates) {
        if (-not $candidate) { continue }
        try {
            $output = & $candidate -c $probe 2>$null
            if ($LASTEXITCODE -eq 0 -and $output) { return ([string]($output | Select-Object -Last 1)).Trim() }
        } catch { }
    }
    $launcher = Get-Command py.exe -ErrorAction SilentlyContinue
    if ($launcher) {
        try {
            $output = & $launcher.Source -3.15 -c $probe 2>$null
            if ($LASTEXITCODE -eq 0 -and $output) { return ([string]($output | Select-Object -Last 1)).Trim() }
        } catch { }
    }
    return $null
}

function Get-SpiceAgent {
    # Discover the installed service from its binary, independent of display language.
    Get-CimInstance Win32_Service | Where-Object { $_.PathName -match '(?i)[\\/]vdservice\.exe(?:"|\s|$)' } | Select-Object -First 1
}

try {
    $python = Find-Python
    if (-not $python) {
        if (-not $PythonIndex) { $PythonIndex = Join-Path $scriptDir 'python\index.json' }
        if (-not (Test-Path -LiteralPath $PythonIndex -PathType Leaf)) {
            throw 'Python 3.15+ is missing. Supply -PythonExe with a bundled runtime, or install Python install manager and supply -PythonIndex with offline runtime media. See README.md.'
        }
        $manager = Get-Command pymanager.exe -ErrorAction SilentlyContinue
        if (-not $manager) { throw 'Install Python install manager before using -PythonIndex, or supply -PythonExe.' }
        & $manager.Source install "--source=$PythonIndex" 3.15
        if ($LASTEXITCODE -ne 0) { throw "Offline Python installation failed ($LASTEXITCODE)." }
        $python = Find-Python
        if (-not $python) { throw 'Python 3.15+ could not be resolved after installation.' }
    }
    Write-Host "Using Python: $python"
    $restartRequired = $false

    if (-not $SkipSpice) {
        $agent = Get-SpiceAgent
        if (-not $agent) {
            $mountedByUs = $false
            try {
                if ($VirtioIso) {
                    $image = Get-DiskImage -ImagePath $VirtioIso
                    if (-not $image.Attached) {
                        Mount-DiskImage -ImagePath $VirtioIso | Out-Null
                        $mountedByUs = $true
                    }
                }
                $media = @($scriptDir)
                $media += @(Get-CimInstance Win32_LogicalDisk -Filter 'DriveType = 5' | ForEach-Object { $_.DeviceID + '\' })
                $installer = $media | ForEach-Object { Join-Path $_ 'virtio-win-guest-tools.exe' } | Where-Object { Test-Path -LiteralPath $_ -PathType Leaf } | Select-Object -First 1
                if (-not $installer) { throw 'Provide mounted VirtIO-Win media, -VirtioIso PATH, or virtio-win-guest-tools.exe beside this script. Use -SkipSpice to omit SPICE clipboard setup.' }
                $process = Start-Process -FilePath $installer -ArgumentList @('/quiet', '/norestart') -Wait -PassThru
                if ($process.ExitCode -notin @(0, 3010, 1641)) { throw "VirtIO guest tools failed ($($process.ExitCode))." }
                if ($process.ExitCode -ne 0) { $restartRequired = $true }
            } finally {
                if ($mountedByUs) { Dismount-DiskImage -ImagePath $VirtioIso | Out-Null }
            }
            if ($restartRequired) {
                Write-Host 'VirtIO guest tools require a restart. Restart Windows and rerun this bootstrapper to complete SSH and IDD setup.'
                exit 3010
            }
            $agent = Get-SpiceAgent
        }
        if ($agent) {
            Start-Service -Name $agent.Name
            (Get-Service -Name $agent.Name).WaitForStatus('Running', [TimeSpan]::FromSeconds(30))
        } else { throw 'SPICE agent service was not installed by the supplied guest tools.' }
    }

    if (-not $SkipSsh) {
        # A non-interactive PowerShell process can still attach a Windows
        # console. Do not let Python's isatty() trigger a hidden password prompt.
        $sshArgs = @((Join-Path $scriptDir 'setup_ssh.py'), '--non-interactive')
        if ($OpenSshSource) { $sshArgs += @('--source', $OpenSshSource) }
        if ($OpenSshZip) { $sshArgs += @('--openssh-zip', $OpenSshZip) }
        & $python @sshArgs
        if ($LASTEXITCODE -ne 0) { throw "SSH setup failed ($LASTEXITCODE)." }
    }
    $iddArgs = @((Join-Path $scriptDir 'setup_vdd.py'))
    if ($IddInstaller) { $iddArgs += @('--installer', $IddInstaller) }
    if ($IddPublisherCertificate) { $iddArgs += @('--publisher-certificate', $IddPublisherCertificate) }
    & $python @iddArgs
    if ($LASTEXITCODE -eq 3010) { $restartRequired = $true }
    elseif ($LASTEXITCODE -ne 0) { throw "Looking Glass IDD setup failed ($LASTEXITCODE)." }

    if ($restartRequired) {
        Write-Host 'Guest installers completed. Restart Windows before verifying display, input and clipboard.'
        exit 3010
    }
    Write-Host 'Requested guest setup steps completed successfully.'
    exit 0
} catch {
    Write-Error -ErrorAction Continue $_
    exit 1
}
