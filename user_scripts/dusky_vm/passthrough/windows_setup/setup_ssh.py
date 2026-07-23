#!/usr/bin/env python3
import os
import sys
import ctypes
import shutil
import subprocess
import zipfile
import winreg
import urllib.request

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
    print("      Dusky VM Guest SSH Configuration Utility         ")
    print("=" * 60)
    
    # Check if sshd service is already installed and running
    sshd_installed = False
    try:
        res = subprocess.run(
            ["powershell", "-NoProfile", "-Command", "Get-Service sshd -ErrorAction SilentlyContinue"],
            capture_output=True, text=True
        )
        if "sshd" in res.stdout:
            sshd_installed = True
    except Exception:
        pass
        
    if not sshd_installed:
        print("[*] OpenSSH Server not detected. Downloading portable Win32-OpenSSH from GitHub...")
        
        # Check if local zip exists on Z: or other common locations to support offline installs
        zip_sources = [
            r"Z:\OpenSSH-Win64.zip",
            r"Z:\a\softwares\OpenSSH-Win64.zip",
            r"C:\OpenSSH-Win64.zip"
        ]
        
        local_zip = None
        for src in zip_sources:
            if os.path.exists(src):
                local_zip = src
                break
                
        target_zip = r"C:\Windows\Temp\OpenSSH-Win64.zip"
        if local_zip:
            print(f"  [OK] Found local OpenSSH zip at: {local_zip}")
            shutil.copy2(local_zip, target_zip)
        else:
            # Download from GitHub (bypasses Windows Update/DISM entirely)
            openssh_url = "https://github.com/PowerShell/Win32-OpenSSH/releases/download/v9.5.0.0p1-Beta/OpenSSH-Win64.zip"
            print("  - Downloading OpenSSH package...")
            try:
                urllib.request.urlretrieve(openssh_url, target_zip)
                print("  [OK] OpenSSH downloaded successfully.")
            except Exception as e:
                print(f"  [ERROR] Failed to download OpenSSH from GitHub: {e}")
                print("  Make sure your VM has internet access or you copy OpenSSH-Win64.zip to Z:\\")
                safe_input("\nPress Enter to exit...", "")
                sys.exit(1)
                
        # Extract package
        install_dir = r"C:\Program Files\OpenSSH-Win64"
        print(f"  - Extracting OpenSSH to {install_dir}...")
        try:
            if os.path.exists(install_dir):
                shutil.rmtree(install_dir)
            
            with zipfile.ZipFile(target_zip, 'r') as zip_ref:
                zip_ref.extractall(r"C:\Program Files")
                
            # The zip contains a folder named "OpenSSH-Win64"
            extracted_folder = r"C:\Program Files\OpenSSH-compat-main"
            if not os.path.exists(install_dir) and os.path.exists(extracted_folder):
                os.rename(extracted_folder, install_dir)
                
            print("  [OK] OpenSSH extracted successfully.")
            if os.path.exists(target_zip):
                os.remove(target_zip)
        except Exception as e:
            print(f"  [ERROR] Failed to extract OpenSSH: {e}")
            safe_input("\nPress Enter to exit...", "")
            sys.exit(1)
            
        # Run the official Microsoft installer script
        print("  - Registering OpenSSH system services...")
        try:
            subprocess.run(
                ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", "install-sshd.ps1"],
                cwd=install_dir, check=True, capture_output=True
            )
            print("  [OK] OpenSSH services registered successfully.")
        except subprocess.CalledProcessError as e:
            print(f"  [ERROR] Failed to run install-sshd.ps1: {e.stderr.decode()}")
            safe_input("\nPress Enter to exit...", "")
            sys.exit(1)
    else:
        print("  [OK] OpenSSH Server is already installed.")
        install_dir = r"C:\Program Files\OpenSSH-Win64"

    # Configure Firewall rule for port 22
    print("  - Setting up inbound Firewall rule for Port 22...")
    try:
        # Check if rule exists
        check_fw = subprocess.run(
            ["netsh", "advfirewall", "firewall", "show", "rule", "name=OpenSSH SSH Server"],
            capture_output=True, text=True
        )
        if "no rules match" in check_fw.stdout.lower() or check_fw.returncode != 0:
            subprocess.run(
                ["netsh", "advfirewall", "firewall", "add", "rule", "name=OpenSSH SSH Server",
                 "dir=in", "action=allow", "protocol=TCP", "localport=22"],
                check=True, capture_output=True
            )
            print("  [OK] Inbound Firewall rule added.")
        else:
            print("  [OK] Inbound Firewall rule already exists.")
    except Exception as e:
        print(f"  [WARNING] Failed to configure Firewall: {e}")

    # Set SSH service startup type to Automatic and start it
    print("  - Configuring SSH service startup type...")
    try:
        subprocess.run(["sc.exe", "config", "sshd", "start=", "auto"], check=True, capture_output=True)
        subprocess.run(["net", "start", "sshd"], capture_output=True)
        print("  [OK] SSHD service set to Automatic and running.")
    except Exception as e:
        print(f"  [WARNING] Failed to configure SSHD service startup: {e}")

    # Set PowerShell as default shell
    print("  - Configuring PowerShell as default SSH shell...")
    try:
        key = winreg.CreateKeyEx(winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\OpenSSH", 0, winreg.KEY_SET_VALUE)
        winreg.SetValueEx(key, "DefaultShell", 0, winreg.REG_SZ, r"C:\Windows\System32\WindowsPowerShell\v1.0\powershell.exe")
        winreg.CloseKey(key)
        print("  [OK] Registry updated (DefaultShell = PowerShell).")
    except Exception as e:
        print(f"  [WARNING] Failed to write default shell registry value: {e}")

    # Set user password
    username = os.environ.get("USERNAME", "dusky")
    print(f"\n[*] Setting login password for user '{username}'...")
    p1 = ""
    if not sys.stdin.isatty():
        print("  - Running non-interactively. Defaulting password to 'ask_the_user'.")
        p1 = "ask_the_user"
    else:
        while True:
            try:
                p1 = input("Enter password (used for SSH login): ").strip()
                p2 = input("Confirm password: ").strip()
            except EOFError:
                print("  - Standard input closed. Defaulting password to 'ask_the_user'.")
                p1 = "ask_the_user"
                break
            if not p1:
                print("[!] Password cannot be empty.")
                continue
            if p1 != p2:
                print("[!] Passwords do not match. Try again.")
                continue
            break
        
    try:
        subprocess.run(["net", "user", username, p1], check=True, capture_output=True)
        print(f"  [OK] Password updated for user '{username}'.")
    except Exception as e:
        print(f"  [ERROR] Failed to set password: {e}")

    # Deploying Public SSH key if provided
    print("\n[*] Setting up public key authentication (optional)...")
    pub_key = ""
    if sys.stdin.isatty():
        try:
            pub_key = input("Paste your host machine's public SSH key (or leave empty to skip): ").strip()
        except EOFError:
            pub_key = ""
    if pub_key:
        ssh_dir = r"C:\ProgramData\ssh"
        auth_keys = os.path.join(ssh_dir, "administrators_authorized_keys")
        try:
            os.makedirs(ssh_dir, exist_ok=True)
            with open(auth_keys, "w", encoding="utf-8") as f:
                f.write(pub_key + "\n")
                
            # Set secure permissions (SYSTEM and Administrators only)
            subprocess.run(
                ["icacls", auth_keys, "/inheritance:r", "/grant", "SYSTEM:(R)", "/grant", "BUILTIN\\Administrators:(R)"],
                check=True, capture_output=True
            )
            print(f"  [OK] Public key added to {auth_keys} with secure ACLs.")
        except Exception as e:
            print(f"  [ERROR] Failed to write authorized_keys: {e}")
    else:
        print("  - Skipping public key deployment.")

    # Restart service to apply all configuration changes
    print("  - Restarting SSH service to apply settings...")
    subprocess.run(["net", "stop", "sshd"], capture_output=True)
    subprocess.run(["net", "start", "sshd"], capture_output=True)
    print("  [OK] SSH Server is running and listening.")
    
    # Check IP configurations for handy output
    ip_cmd = "(Get-NetIPAddress -AddressFamily IPv4 | Where-Object { $_.InterfaceAlias -notlike '*Loopback*' }).IPAddress"
    try:
        res = subprocess.run(["powershell", "-NoProfile", "-Command", ip_cmd], capture_output=True, text=True)
        ips = res.stdout.strip().splitlines()
        print("\n>>> SSH SETUP COMPLETED SUCCESSFULLY! <<<")
        print("You can connect to this VM from your host using:")
        for ip in ips:
            print(f"  ssh {username}@{ip}")
    except Exception:
        pass
        
    safe_input("\nPress Enter to complete SSH configuration...", "")

if __name__ == "__main__":
    main()
