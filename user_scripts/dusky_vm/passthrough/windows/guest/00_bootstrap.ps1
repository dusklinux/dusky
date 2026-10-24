<#
.SYNOPSIS
    Dusky VM Guest Setup Bootstrapper
    Description: Verifies Python 3, installs it if missing, and then runs setup_ssh.py and setup_vdd.py.
    Requirements: Run as Administrator in PowerShell.
#>

# 1. Enforce Administrator Privileges
$isAdmin = ([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
if (-not $isAdmin) {
    Write-Host "Re-launching bootstrapper as Administrator..." -ForegroundColor Yellow
    Start-Process powershell -ArgumentList "-NoProfile -ExecutionPolicy Bypass -File `"$PSCommandPath`"" -Verb RunAs
    Exit
}

Write-Host "==================================================" -ForegroundColor Cyan
Write-Host "      Dusky VM Guest Setup Bootstrapper           " -ForegroundColor Cyan
Write-Host "==================================================" -ForegroundColor Cyan

# 2. Check for Python 3 (run it to verify it's not a fake Microsoft Store app alias)
$pythonInstalled = $false
try {
    $out = & python --version 2>&1
    if ($LASTEXITCODE -eq 0 -and $out -like "*Python*") {
        $pythonInstalled = $true
    }
} catch {
    $pythonInstalled = $false
}

if (-not $pythonInstalled) {
    Write-Host "`n[!] Python 3 not found on this system." -ForegroundColor Yellow
    Write-Host "[*] Downloading the latest Python 3.13 installer from python.org..." -ForegroundColor Cyan
    
    $tempDir = [System.IO.Path]::GetTempPath()
    $installerPath = Join-Path $tempDir "python_installer.exe"
    $pythonUrl = "https://www.python.org/ftp/python/3.13.0/python-3.13.0-amd64.exe"
    
    try {
        Invoke-WebRequest -Uri $pythonUrl -OutFile $installerPath -ErrorAction Stop
        Write-Host "[+] Python installer downloaded successfully." -ForegroundColor Green
    } catch {
        Write-Error "Failed to download Python installer: $_"
        Read-Host "Press Enter to exit..."
        Exit 1
    }
    
    Write-Host "[*] Running Python installation with progress bar... (this will take a moment)" -ForegroundColor Cyan
    # Install for all users and add python.exe to PATH
    $installArgs = "/passive InstallAllUsers=1 PrependPath=1 TargetDir=`"C:\Program Files\Python313`""
    $process = Start-Process -FilePath $installerPath -ArgumentList $installArgs -Wait -PassThru
    
    if ($process.ExitCode -eq 0) {
        Write-Host "[+] Python 3 installed successfully!" -ForegroundColor Green
        Remove-Item $installerPath -Force
        
        # Force refresh PATH environment variables for the current session
        $env:Path = [System.Environment]::GetEnvironmentVariable("Path", "Machine") + ";" + [System.Environment]::GetEnvironmentVariable("Path", "User")
    } else {
        Write-Error "Python installation failed with exit code: $($process.ExitCode)"
        Read-Host "Press Enter to exit..."
        Exit 1
    }
} else {
    Write-Host "`n[+] Python 3 is already installed." -ForegroundColor Green
}

# 2.5 Ensure SPICE Guest Agent (clipboard sharing) is installed
$spiceAgent = Get-Service -Name "spice-agent" -ErrorAction SilentlyContinue
if (-not $spiceAgent -or $spiceAgent.Status -ne "Running") {
    Write-Host "`n[*] SPICE Guest Agent is not running." -ForegroundColor Yellow
    
    $choice = Read-Host "Would you like to install the SPICE Guest Agent from the VirtIO ISO? [Y/n]"
    if ($null -eq $choice -or $choice -match '^\s*$' -or $choice.Trim().ToLower() -eq 'y') {
        # Locate the VirtIO-Win ISO in the root of the shared Z: drive (or current directory)
        $isoPath = Get-ChildItem -Path "Z:\" -Filter "virtio-win-*.iso" | Select-Object -First 1 -ExpandProperty FullName
        if (-not $isoPath) {
            $isoPath = Get-ChildItem -Path "." -Filter "virtio-win-*.iso" | Select-Object -First 1 -ExpandProperty FullName
        }
        
        if ($isoPath) {
            Write-Host "[*] Found VirtIO-Win ISO at: $isoPath" -ForegroundColor Cyan
            Write-Host "[*] Mounting ISO to search for guest tools..." -ForegroundColor Cyan
            try {
                $mount = Mount-DiskImage -ImagePath $isoPath -PassThru -ErrorAction Stop
                $driveLetter = ($mount | Get-Volume).DriveLetter
                if ($driveLetter) {
                    $installer = Join-Path "${driveLetter}:" "virtio-win-guest-tools.exe"
                    if (Test-Path $installer) {
                        Write-Host "[*] Running VirtIO guest tools installer silently..." -ForegroundColor Cyan
                        $proc = Start-Process -FilePath $installer -ArgumentList "/quiet /norestart" -PassThru -Wait
                        if ($proc.ExitCode -eq 0) {
                            Write-Host "[+] SPICE Guest Agent and VirtIO tools installed successfully!" -ForegroundColor Green
                        } else {
                            Write-Warning "VirtIO guest tools installation completed with non-zero exit code: $($proc.ExitCode)"
                        }
                    } else {
                        Write-Warning "Could not find 'virtio-win-guest-tools.exe' inside the mounted ISO. Skipping installation..."
                    }
                } else {
                    Write-Warning "Could not resolve drive letter for mounted ISO. Skipping installation..."
                }
                Dismount-DiskImage -ImagePath $isoPath -ErrorAction SilentlyContinue | Out-Null
            } catch {
                Write-Warning "Failed to mount/read VirtIO ISO: $_. Skipping installation..."
            }
        } else {
            Write-Warning "Could not locate virtio-win-*.iso on Z:\ or current directory. Skipping installation..."
        }
    } else {
        Write-Host "[-] Skipping SPICE Guest Agent installation." -ForegroundColor Yellow
    }
} else {
    Write-Host "`n[+] SPICE Guest Agent is already running." -ForegroundColor Green
}


# 3. Locate and execute the Python setup scripts
$scriptDir = Split-Path -Path $MyInvocation.MyCommand.Definition -Parent
$sshScript = Join-Path $scriptDir "setup_ssh.py"
$vddScript = Join-Path $scriptDir "setup_vdd.py"

if (-not (Test-Path $sshScript)) { $sshScript = Join-Path (Get-Location) "setup_ssh.py" }
if (-not (Test-Path $vddScript)) { $vddScript = Join-Path (Get-Location) "setup_vdd.py" }

if (Test-Path $sshScript) {
    Write-Host "`n[*] Launching SSH Configuration Script..." -ForegroundColor Cyan
    & python $sshScript
} else {
    Write-Warning "Could not find 'setup_ssh.py' in $scriptDir or current directory."
}

if (Test-Path $vddScript) {
    Write-Host "`n[*] Launching VDD Setup Script..." -ForegroundColor Cyan
    & python $vddScript
} else {
    Write-Warning "Could not find 'setup_vdd.py' in $scriptDir or current directory."
}

Write-Host "`n==================================================" -ForegroundColor Green
Write-Host "      ALL VM GUEST SETUPS COMPLETED SUCCESSFULLY! " -ForegroundColor Green
Write-Host "==================================================" -ForegroundColor Green
Read-Host "Press Enter to close..."
