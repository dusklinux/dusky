# Windows Guest Setup & Software Downloads

This guide covers the necessary software to install inside your Windows Virtual Machine (VM) to enable Looking Glass and ensure the system runs smoothly.

> [!TIP] Workflow
> 
> Perform these steps inside your Windows VM. You can use the default SPICE view (Virt-Manager window) to download and install these files before Looking Glass is fully active.

## 1. Core Dependencies

Before installing the display drivers, you must ensure the C++ runtime is installed.

### Microsoft Visual C++ Redistributable

Required for the Virtual Display Driver and other tools to function.

1. Download the **latest supported Visual C++ Redistributable**.
    
2. Run the installer.
    

```http
https://learn.microsoft.com/en-us/cpp/windows/latest-supported-vc-redist
```

## 2. Virtual Display Driver (VDD)

This driver creates a "monitor" that exists only in memory, which Looking Glass allows you to view.

**Official Repository:**
```http
https://github.com/VirtualDrivers/Virtual-Display-Driver
```

### Installation Option A: GUI Installation
1. Download the latest release `.zip` file from the repository releases page.
2. Extract the archive.
3. Run `VirtualDriverControl.exe` and use the GUI to install the driver.

### Installation Option B: CLI Staging (Recommended for automation)
If you prefer a scriptable CLI-only installation or want to avoid GUI menus:

1. Download and extract the driver files (`VirtualDisplayDriver.inf`, `VirtualDisplayDriver.cer`, `VirtualDisplayDriver.sys`) to `C:\VirtualDisplayDriver`.
2. Trust the driver certificate by adding it to the system stores (run in Administrator command prompt/PowerShell):
   ```cmd
   certutil.exe -addstore "TrustedPublisher" C:\VirtualDisplayDriver\VirtualDisplayDriver.cer
   certutil.exe -addstore "Root" C:\VirtualDisplayDriver\VirtualDisplayDriver.cer
   ```
3. Register and install the driver using **one** of the following tools:
   * **Using `devcon.exe`** (creates a fresh hardware root node):
     ```cmd
     devcon.exe install C:\VirtualDisplayDriver\VirtualDisplayDriver.inf Root\MttVDD
     ```
   * **Using `pnputil.exe`** (modern built-in utility):
     ```cmd
     pnputil.exe /add-driver C:\VirtualDisplayDriver\VirtualDisplayDriver.inf /install
     ```

### Configuration (SDR Constraints)
Configure `C:\VirtualDisplayDriver\vdd_settings.xml` to match your target memory size and limit the resolution to SDR to prevent buffer overflow issues on Windows 10:

```xml
<?xml version='1.0' encoding='utf-8'?>
<VirtualDisplaySettings>
   <Monitors>1</Monitors>
   <Resolution>
       <Width>2560</Width>
       <Height>1440</Height>
       <RefreshRate>144</RefreshRate>
   </Resolution>
</VirtualDisplaySettings>
```

> [!DANGER] CRITICAL WARNING
> 
> Only install this driver ONCE.
> 
> Do not run the installer or commands multiple times, or you will create multiple "ghost" monitors, which will cause your cursor to constantly move to other invisible monitors when the cursor touches the screen edge. 

## 3. Looking Glass Host

This is the software that runs inside Windows and sends the video feed to your Linux host.

1. Download the **Windows Host Binary**
2. Run the installer.
    

**Official Website:**

```http
https://looking-glass.io/downloads
```
**GitHub (Alternative):**
```http
https://github.com/gnif/LookingGlass
```

## 4. Graphics Drivers

You must install the official NVIDIA drivers for your passed-through GPU.

1. Download the driver for your specific card.
    
2. Install it as you would on a normal PC.
    

```http
https://www.nvidia.com/en-us/drivers/
```

> [!WARNING] **DO NOT DISABLE DISPLAY DRIVERS**
> 
> - **Never** disable the NVIDIA Display Driver in Device Manager.
>     
> - **Never** disable the Virtual Display Driver (VDD) in Device Manager.
>     
> 
> If you somehow end up with _two_ Virtual Display Drivers, be extremely careful. If you disable the active one, you will lose video output. If you are unsure, restart the VM and check again before making changes.

## 5. System Utilities

### 7-Zip

A better file archiver (required for extracting some driver files).

```http
https://www.7-zip.org/
```

### O&O ShutUp10++

A free antispy tool to disable Windows telemetry and unwanted updates.

```http
https://www.oo-software.com/en/download/current/ooshutup10
```

### Windows Update MiniTool

Allows you to control exactly which Windows updates are installed, preventing Microsoft from overwriting your custom iso especially if you have it debloated of defender and other stuff. 

```http
https://www.majorgeeks.com/files/details/windows_update_minitool.html
```

### WinFSP should already have been installed in one of the previous steps, if not , here's the link. 
```http
https://github.com/winfsp/winfsp
```

## 6. OpenSSH Server Setup (Host-to-Guest Remote CLI Access)

Using the built-in Windows `DISM /Add-Capability` tool to install OpenSSH frequently hangs due to Windows Update service locks on fresh VMs. Instead, install the official Microsoft Win32-OpenSSH portable release manually.

### Installation Steps (inside the Windows VM)
1. Download the latest `OpenSSH-Win64.zip` release from the official Microsoft PowerShell/Win32-OpenSSH GitHub repository:
   ```http
   https://github.com/PowerShell/Win32-OpenSSH/releases
   ```
2. Extract the archive to `C:\Program Files\OpenSSH-Win64`.
3. Open an Administrator PowerShell window and execute the service registration script:
   ```powershell
   powershell.exe -ExecutionPolicy Bypass -File "C:\Program Files\OpenSSH-Win64\install-sshd.ps1"
   ```
4. Configure the SSH service to start automatically and start it:
   ```powershell
   Set-Service -Name "sshd" -StartupType Automatic
   Start-Service -Name "sshd"
   ```
5. Open port 22 in the Windows Defender Firewall:
   ```powershell
   New-NetFirewallRule -Name "SSH" -DisplayName "OpenSSH SSH Server" -Enabled True -Direction Inbound -Protocol TCP -Action Allow -LocalPort 22
   ```
6. Set PowerShell as the default shell for SSH connections:
   ```powershell
   New-ItemProperty -Path "HKLM:\SOFTWARE\OpenSSH" -Name "DefaultShell" -Value "C:\Windows\System32\WindowsPowerShell\v1.0\powershell.exe" -PropertyType String -Force
   ```

## 7. Troubleshooting & Specific Quirks

### VirtIO-FS Service

If you configured filesystem sharing, the **VirtIO-FS Service** will only appear in your services list _after_ the `virtio-win` ISO drivers are fully installed.


### Cursor Disappearing

Sometimes, enabling the VirtIO mouse driver causes the cursor to vanish.

- **Fix:** Uninstall the mouse driver in Device Manager, then reinstall it while viewing the VM through the Looking Glass client.
or 
**(THIS ONE WORKS, TESTED!!)**
**uninstall the virio driver using the x64 file in cd drive virtio and then resintall it after restarting the vm.** 
