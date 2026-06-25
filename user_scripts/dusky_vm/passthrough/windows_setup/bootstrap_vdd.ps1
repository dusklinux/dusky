<#
.SYNOPSIS
    Python Bootstrapper and VDD Installer
    Description: Verifies Python 3 installation, installs it if missing, then launches install_vdd.py.
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
Write-Host "      Python 3 & VDD Setup Bootstrapper           " -ForegroundColor Cyan
Write-Host "==================================================" -ForegroundColor Cyan

# 2. Check for Python 3
$pythonCheck = Get-Command python -ErrorAction SilentlyContinue

if (-not $pythonCheck) {
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
    
    Write-Host "[*] Running Python installation silently... (this will take a moment)" -ForegroundColor Cyan
    # Install for all users and add python.exe to PATH
    $installArgs = "/quiet InstallAllUsers=1 PrependPath=1 TargetDir=`"C:\Program Files\Python313`""
    $process = Start-Process -FilePath $installerPath -ArgumentList $installArgs -Wait -PassThru
    
    if ($process.ExitCode -eq 0) {
        Write-Host "[+] Python 3 installed successfully!" -ForegroundColor Green
        # Clean up installer
        Remove-Item $installerPath -Force
        
        # Force refresh PATH environment variables for the current session
        $env:Path = [System.Environment]::GetEnvironmentVariable("Path", "Machine") + ";" + [System.Environment]::GetEnvironmentVariable("Path", "User")
    } else {
        Write-Error "Python installation failed with exit code: $($process.ExitCode)"
        Read-Host "Press Enter to exit..."
        Exit 1
    }
} else {
    Write-Host "`n[+] Python 3 is already installed: $($pythonCheck.Source)" -ForegroundColor Green
}

# 3. Locate and execute the Python installer script
$scriptDir = Split-Path -Path $MyInvocation.MyCommand.Definition -Parent
$pythonScript = Join-Path $scriptDir "install_vdd.py"

if (-not (Test-Path $pythonScript)) {
    # If not found in the same folder, look in the current working directory
    $pythonScript = Join-Path (Get-Location) "install_vdd.py"
}

if (Test-Path $pythonScript) {
    Write-Host "`n[*] Launching Python VDD Installer..." -ForegroundColor Cyan
    # Run the Python installer
    & python $pythonScript
} else {
    Write-Error "Could not find 'install_vdd.py' in $scriptDir or current directory."
    Read-Host "Press Enter to exit..."
    Exit 1
}
