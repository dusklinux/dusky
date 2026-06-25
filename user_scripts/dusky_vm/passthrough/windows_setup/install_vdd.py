import os
import sys
import ctypes
import shutil
import subprocess
import time

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

def main():
    elevate_privileges()
    
    print("=" * 50)
    print("   Virtual Display Driver (VDD) Python Setup Tool   ")
    print("=" * 50)
    
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
        choice = input("Use this path? (Y/n): ").strip().lower()
        if choice != 'n':
            driver_path = detected_path
            
    while not driver_path:
        print(f"\n[-] Please enter the folder path containing 'MttVDD.inf' and 'mttvdd.cat':")
        input_path = input("Path: ").strip()
        # Clean quotes if user dragged and dropped
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
        input("\nPress Enter to exit...")
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
            print("  ✓ Driver certificate successfully trusted.")
        else:
            print("  ⚠ Could not trust certificate. Driver install might fail.")
    except Exception as e:
        print(f"  ✖ Failed to import certificate: {e}")
        input("\nPress Enter to exit...")
        sys.exit(1)
        
    # 4. Register and Install Driver via pnputil
    print("\n[3/3] Installing driver via pnputil...")
    try:
        res = subprocess.run(
            ["pnputil", "/add-driver", inf_file, "/install"],
            capture_output=True, text=True, check=True
        )
        print("  ✓ Driver registered and installed successfully.")
        print(res.stdout.strip())
    except Exception as e:
        print(f"  ✖ Failed to register driver: {e}")
        input("\nPress Enter to exit...")
        sys.exit(1)
        
    # 5. Restart Looking Glass Service
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
            print("  ✓ Looking Glass host service restarted successfully.")
        else:
            print("  - Looking Glass host service is not installed on this system.")
    except Exception:
        pass
        
    # 6. Verify Installation
    print("\n" + "=" * 50)
    print("               INSTALLATION DIAGNOSTICS           ")
    print("=" * 50)
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
                print("\n[SUCCESS] Driver is active and running!")
            else:
                print("\n[WARNING] Driver detected but has a status issue. Check Device Manager.")
        else:
            print("[!] Driver not found in display class. A VM restart may be required.")
    except Exception as e:
        print(f"Error querying status: {e}")
        
    print("=" * 50)
    input("\nPress Enter to exit...")

if __name__ == "__main__":
    main()
