<#
.SYNOPSIS
    Interactive Virtual Display Driver (VDD) Auto-Installer
    Description: Automates certificate trust and driver installation inside the Windows VM.
    Requirements: Run as Administrator in PowerShell.
#>

# 1. Enforce Administrator Privileges
$isAdmin = ([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
if (-not $isAdmin) {
    Write-Host "Re-launching script as Administrator..." -ForegroundColor Yellow
    Start-Process powershell -ArgumentList "-NoProfile -ExecutionPolicy Bypass -File `"$PSCommandPath`"" -Verb RunAs
    Exit
}

Write-Host "==================================================" -ForegroundColor Cyan
Write-Host "   Virtual Display Driver (VDD) Setup Utility     " -ForegroundColor Cyan
Write-Host "==================================================" -ForegroundColor Cyan

# 2. Intelligent Driver Path Auto-Detection
$commonPaths = @(
    "Z:\a\softwares\vdd\VDD.Control.25.7.23\SignedDrivers\x86\VDD",
    "Z:\VirtualDisplayDriver",
    "C:\VirtualDisplayDriver",
    "Z:\a\softwares\vdd\VDD.Control.25.7.23\SignedDrivers\ARM64\VDD"
)

$detectedPath = $null
foreach ($path in $commonPaths) {
    if (Test-Path "$path\MttVDD.inf") {
        $detectedPath = $path
        break
    }
}

$driverPath = ""
if ($detectedPath) {
    Write-Host "`n[+] Auto-detected VDD files at: $detectedPath" -ForegroundColor Green
    $choice = Read-Host "Use this path? (Y/n)"
    if ($choice.Trim().ToLower() -ne "n") {
        $driverPath = $detectedPath
    }
}

# If no path detected or user declined, prompt interactively
while (-not $driverPath) {
    Write-Host "`n[-] Please enter the folder path containing 'MttVDD.inf' and 'mttvdd.cat':" -ForegroundColor Yellow
    $inputPath = (Read-Host "Path").Trim()
    
    # Remove quotes if user dragged and dropped the folder
    $inputPath = $inputPath -replace '^"|"$', ''
    
    if (Test-Path "$inputPath\MttVDD.inf") {
        $driverPath = $inputPath
    } else {
        Write-Host "[!] Invalid path. Could not find 'MttVDD.inf' in: $inputPath" -ForegroundColor Red
    }
}

$infFile = "$driverPath\MttVDD.inf"
$catFile = "$driverPath\mttvdd.cat"
$dllFile = "$driverPath\MttVDD.dll"

# Verify all required files are present
if (-not (Test-Path $catFile) -or -not (Test-Path $dllFile)) {
    Write-Error "Missing required files (mttvdd.cat or MttVDD.dll) in: $driverPath"
    Read-Host "Press Enter to exit..."
    Exit 1
}

# 3. Import Catalog Certificate to Local Machine Stores to Trust the Driver
Write-Host "`n[1/3] Importing driver Authenticode certificate to establish trust..." -ForegroundColor Yellow
try {
    $sig = Get-AuthenticodeSignature $catFile -ErrorAction Stop
    if ($sig.SignerCertificate) {
        # Trusted Publishers Store
        $store1 = New-Object System.Security.Cryptography.X509Certificates.X509Store("TrustedPublisher", "LocalMachine")
        $store1.Open("ReadWrite")
        $store1.Add($sig.SignerCertificate)
        $store1.Close()

        # Trusted Root Certification Authorities Store
        $store2 = New-Object System.Security.Cryptography.X509Certificates.X509Store("Root", "LocalMachine")
        $store2.Open("ReadWrite")
        $store2.Add($sig.SignerCertificate)
        $store2.Close()

        Write-Host "[OK] Driver certificate imported into TrustedPublisher and Root stores." -ForegroundColor Green
    } else {
        Write-Warning "Could not retrieve signer certificate from $catFile. Installation might prompt for manual trust."
    }
} catch {
    Write-Error "Failed to import driver certificate: $_"
    Read-Host "Press Enter to exit..."
    Exit 1
}

# 4. Install the Driver using pnputil
Write-Host "`n[2/3] Registering and installing driver via pnputil..." -ForegroundColor Yellow
try {
    # Copy files locally if installing from network share to ensure Windows keeps a local copy
    $localTarget = "C:\VirtualDisplayDriver"
    if ($driverPath -ne $localTarget) {
        Write-Host "Copying driver files locally to $localTarget to guarantee stability..." -ForegroundColor Cyan
        if (-not (Test-Path $localTarget)) {
            New-Item -ItemType Directory -Path $localTarget -Force | Out-Null
        }
        Copy-Item -Path "$driverPath\*" -Destination $localTarget -Force
        $infFile = "$localTarget\MttVDD.inf"
    }

    # Install using pnputil
    pnputil /add-driver $infFile /install
    Write-Host "[OK] Virtual Display Driver successfully registered and installed." -ForegroundColor Green
} catch {
    Write-Error "Failed to install driver: $_"
    Read-Host "Press Enter to exit..."
    Exit 1
}

# 5. Handle Looking Glass Host Service restart
Write-Host "`n[3/3] Querying Looking Glass Host Service..." -ForegroundColor Yellow
$lgService = Get-Service -Name "Looking Glass (host)" -ErrorAction SilentlyContinue
if ($lgService) {
    try {
        if ($lgService.Status -ne "Running") {
            Start-Service -Name "Looking Glass (host)" -ErrorAction Stop
            Write-Host "[OK] Looking Glass host service started successfully." -ForegroundColor Green
        } else {
            Restart-Service -Name "Looking Glass (host)" -ErrorAction Stop
            Write-Host "[OK] Looking Glass host service restarted successfully." -ForegroundColor Green
        }
    } catch {
        Write-Warning "Could not manage Looking Glass service: $_"
    }
} else {
    Write-Host "[INFO] Looking Glass host service is not installed yet. Skipping service startup." -ForegroundColor Cyan
}

# 6. Verify Installation Diagnostics
Write-Host "`n==================================================" -ForegroundColor Green
Write-Host "               INSTALLATION STATUS                " -ForegroundColor Green
Write-Host "==================================================" -ForegroundColor Green

$dev = Get-PnpDevice -Class Display | Where-Object { $_.FriendlyName -like "*Virtual Display*" -or $_.FriendlyName -like "*IddSampleDriver*" }
if ($dev) {
    Write-Host "Device Name: $($dev.FriendlyName)" -ForegroundColor Green
    Write-Host "Status     : $($dev.Status)" -ForegroundColor Green
    if ($dev.Status -eq "OK") {
        Write-Host "`n[SUCCESS] Driver is active and running!" -ForegroundColor Green
    } else {
        Write-Host "`n[WARNING] Driver found but in state: $($dev.Status). Check Device Manager." -ForegroundColor Yellow
    }
} else {
    Write-Host "[!] Driver device not found in display class. Try restarting the VM." -ForegroundColor Red
}
Write-Host "==================================================" -ForegroundColor Green

Read-Host "`nPress Enter to close this window..."
