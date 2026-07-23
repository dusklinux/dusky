#!/usr/bin/env python3
import os
import sys
import ctypes
import shutil
import subprocess

def is_admin():
    try:
        return ctypes.windll.shell32.IsUserAnAdmin()
    except Exception:
        return False

def elevate_privileges():
    if not is_admin():
        print("[*] Requesting administrative privileges...")
        # Re-run the script with admin privileges
        ctypes.windll.shell32.ShellExecuteW(
            None, "runas", sys.executable, " ".join(sys.argv), None, 1
        )
        sys.exit(0)

def safe_input(prompt="", default=""):
    try:
        return input(prompt)
    except EOFError:
        print("  - Standard input not available. Using default.")
        return default

def main():
    elevate_privileges()
    
    print("=" * 60)
    print("   Virtual Display Driver (VDD) Python Setup Tool   ")
    print("=" * 60)
    
    # 1. Driver path auto-detection
    common_paths = [
        r"Z:\a\softwares\vdd\VDD.Control.25.7.23\SignedDrivers\x86\VDD",
        r"Z:\VirtualDisplayDriver",
        r"C:\VirtualDisplayDriver",
        r"Z:\a\softwares\vdd\VDD.Control.25.7.23\SignedDrivers\ARM64\VDD"
    ]
    
    detected_path = None
    for path in common_paths:
        if os.path.exists(os.path.join(path, "MttVDD.inf")):
            detected_path = path
            break
            
    driver_path = ""
    if detected_path:
        print(f"\n[+] Auto-detected VDD files at: {detected_path}")
        choice = safe_input("Use this path? (Y/n): ", "y").strip().lower()
        if choice != 'n':
            driver_path = detected_path
            
    while not driver_path:
        print(f"\n[-] Please enter the folder path containing 'MttVDD.inf' and 'mttvdd.cat':")
        input_path = safe_input("Path: ").strip()
        if not input_path:
            print("[FATAL] Standard input not available and path not provided. Exiting.")
            safe_input("\nPress Enter to exit...", "")
            sys.exit(1)
        input_path = input_path.replace('"', '').replace("'", "")
        
        if os.path.exists(os.path.join(input_path, "MttVDD.inf")):
            driver_path = input_path
        else:
            print(f"[!] Invalid path. Could not find 'MttVDD.inf' in: {input_path}")
            
    inf_file = os.path.join(driver_path, "MttVDD.inf")
    cat_file = os.path.join(driver_path, "mttvdd.cat")
    dll_file = os.path.join(driver_path, "MttVDD.dll")
    
    if not os.path.exists(cat_file) or not os.path.exists(dll_file):
        print(f"[FATAL] Missing mttvdd.cat or MttVDD.dll in: {driver_path}")
        safe_input("\nPress Enter to exit...", "")
        sys.exit(1)
        
    # 2. Stage files locally
    local_target = r"C:\VirtualDisplayDriver"
    if driver_path.lower() != local_target.lower():
        print(f"\n[1/3] Copying driver files locally to {local_target}...")
        os.makedirs(local_target, exist_ok=True)
        for item in os.listdir(driver_path):
            s_file = os.path.join(driver_path, item)
            d_file = os.path.join(local_target, item)
            if os.path.isfile(s_file):
                shutil.copy2(s_file, d_file)
        
        # Copy devcon.exe from the Dependencies folder if found
        devcon_src = None
        possible_devcon_paths = [
            os.path.join(driver_path, "..", "..", "..", "Dependencies", "devcon.exe"),
            os.path.join(driver_path, "devcon.exe"),
            r"Z:\a\softwares\vdd\VDD.Control.25.7.23\Dependencies\devcon.exe"
        ]
        for p in possible_devcon_paths:
            normalized = os.path.abspath(p)
            if os.path.exists(normalized):
                devcon_src = normalized
                break
        if devcon_src:
            shutil.copy2(devcon_src, os.path.join(local_target, "devcon.exe"))
            print("  - Staged devcon.exe utility locally.")
            
        inf_file = os.path.join(local_target, "MttVDD.inf")
        cat_file = os.path.join(local_target, "mttvdd.cat")
        
    # 3. Trust the Self-Signed Certificate via PowerShell
    print("\n[2/3] Importing driver Authenticode certificate to trust stores...")
    ps_command = f"""
    $sig = Get-AuthenticodeSignature "{cat_file}"
    if ($sig.SignerCertificate) {{
        $store1 = New-Object System.Security.Cryptography.X509Certificates.X509Store("TrustedPublisher", "LocalMachine")
        $store1.Open("ReadWrite")
        $store1.Add($sig.SignerCertificate)
        $store1.Close()

        $store2 = New-Object System.Security.Cryptography.X509Certificates.X509Store("Root", "LocalMachine")
        $store2.Open("ReadWrite")
        $store2.Add($sig.SignerCertificate)
        $store2.Close()
        Write-Host "Success"
    }} else {{
        Write-Host "Failed"
    }}
    """
    
    try:
        res = subprocess.run(
            ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", ps_command],
            capture_output=True, text=True, check=True
        )
        if "Success" in res.stdout:
            print("  [OK] Driver certificate successfully trusted.")
        else:
            print("  [WARNING] Could not trust certificate. Driver install might fail.")
    except Exception as e:
        print(f"  [ERROR] Failed to import certificate: {e}")
        safe_input("\nPress Enter to exit...", "")
        sys.exit(1)
        
    # Check if the device already exists in the system
    device_exists = False
    try:
        check_cmd = r'Get-PnpDevice -HardwareID "Root\MttVDD" -ErrorAction SilentlyContinue'
        res = subprocess.run(
            ["powershell", "-NoProfile", "-Command", check_cmd],
            capture_output=True, text=True, check=True
        )
        if "MttVDD" in res.stdout:
            device_exists = True
    except Exception:
        pass

    if not device_exists:
        print("\n[3/3] Fresh VM detected. Creating device node and installing driver via devcon...")
        devcon_path = os.path.join(local_target, "devcon.exe")
        if os.path.exists(devcon_path):
            try:
                res = subprocess.run(
                    [devcon_path, "install", inf_file, "Root\\MttVDD"],
                    capture_output=True, text=True, check=True
                )
                print("  [OK] Device node created and driver installed successfully.")
                print(res.stdout.strip())
            except Exception as e:
                print(f"  [ERROR] devcon failed to create device node: {e}")
                safe_input("\nPress Enter to exit...", "")
                sys.exit(1)
        else:
            print("  [WARNING] devcon.exe not found. Attempting fallback via standard pnputil...")
            try:
                res = subprocess.run(
                    ["pnputil", "/add-driver", inf_file, "/install"],
                    capture_output=True, text=True, check=True
                )
                print("  [OK] Driver registered successfully via pnputil (device node may still need creation).")
                print(res.stdout.strip())
            except Exception as e:
                print(f"  [ERROR] Failed to register driver: {e}")
                safe_input("\nPress Enter to exit...", "")
                sys.exit(1)
    else:
        print("\n[3/3] Existing device detected. Updating driver via pnputil...")
        try:
            res = subprocess.run(
                ["pnputil", "/add-driver", inf_file, "/install"],
                capture_output=True, text=True, check=True
            )
            print("  [OK] Driver registered and updated successfully.")
            print(res.stdout.strip())
        except Exception as e:
            print(f"  [ERROR] Failed to update driver: {e}")
            safe_input("\nPress Enter to exit...", "")
            sys.exit(1)
            
    # Restart Looking Glass Service
    print("\n[*] Querying Looking Glass service...")
    ps_service_cmd = """
    $lgService = Get-Service -Name "Looking Glass (host)" -ErrorAction SilentlyContinue
    if ($lgService) {
        if ($lgService.Status -ne "Running") {
            Start-Service -Name "Looking Glass (host)" -ErrorAction Stop
        } else {
            Restart-Service -Name "Looking Glass (host)" -ErrorAction Stop
        }
        Write-Host "Restarted"
    } else {
        Write-Host "NotFound"
    }
    """
    try:
        res = subprocess.run(
            ["powershell", "-NoProfile", "-Command", ps_service_cmd],
            capture_output=True, text=True, check=True
        )
        if "Restarted" in res.stdout:
            print("  [OK] Looking Glass host service restarted successfully.")
        else:
            print("  - Looking Glass host service is not installed on this system.")
    except Exception:
        pass
        
    print("\n" + "=" * 60)
    print("                  INSTALLATION STATUS                     ")
    print("=" * 60)
    ps_verify_cmd = """
    $dev = Get-PnpDevice -Class Display | Where-Object { $_.FriendlyName -like "*Virtual Display*" -or $_.FriendlyName -like "*IddSampleDriver*" }
    if ($dev) {
        Write-Host "Device: $($dev.FriendlyName)"
        Write-Host "Status: $($dev.Status)"
    } else {
        Write-Host "NotFound"
    }
    """
    try:
        res = subprocess.run(
            ["powershell", "-NoProfile", "-Command", ps_verify_cmd],
            capture_output=True, text=True, check=True
        )
        out = res.stdout.strip()
        if "NotFound" not in out:
            print(out)
            if "Status: OK" in out:
                print("\n[SUCCESS] Virtual Display Driver is active and running!")
            else:
                print("\n[WARNING] Driver detected but has a status issue. Check Device Manager.")
        else:
            print("[!] Driver not found in display class. A VM restart may be required.")
    except Exception as e:
        print(f"Error querying status: {e}")
        
    print("=" * 60)
    safe_input("\nPress Enter to complete VDD installation...", "")

if __name__ == "__main__":
    main()
