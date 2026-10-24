# Dusky VM Guest Setup Guide

This guide details the sequence of scripts to run inside a fresh Windows VM to configure SSH (without DISM network hangs) and install the Virtual Display Driver (VDD) for Looking Glass.

---

## The Setup Files

The host source files are in [windows/guest](../../windows/guest/). Copy that
directory's three scripts together to the guest-accessible location used below.

All scripts are located on the shared `Z:` drive (mapped to `/mnt/zram1` on the host) and are named descriptively to reflect their specific functions:

* **`00_bootstrap.ps1`**: The main entry point script. It verifies Python 3 is installed, automatically downloads and silently installs Python 3.13 from python.org if missing, and then runs the setup scripts sequentially.
* **`setup_ssh.py`**: The Python setup script that:
  1. Downloads and extracts the official Microsoft Win32-OpenSSH portable package (bypassing the slow and error-prone `DISM /Add-Capability` tool entirely to avoid Windows Update hangs).
  2. Installs and registers the SSH system service.
  3. Configures default shell (PowerShell), inbound firewall rules, user login password, and SSH key authentication.
* **`setup_vdd.py`**: The Python setup script that:
  1. Auto-detects local Virtual Display Driver (VDD) files on `Z:\` and copy-stages them locally.
  2. Registers the developer signature certificate to establish trust.
  3. Dynamically creates the `Root\MttVDD` hardware node using `devcon.exe` (critical for fresh VM setups) and installs the driver.

---

## How to Run the Installer

1. Open **PowerShell** as **Administrator** inside the Windows VM.
2. Run the bootstrapper script:
   ```powershell
   Set-ExecutionPolicy Bypass -Scope Process -Force
   & "Z:\00_bootstrap.ps1"
   ```

The script will handle Python installation, SSH configuration, and Virtual Display Driver installation in a single, robust process.
