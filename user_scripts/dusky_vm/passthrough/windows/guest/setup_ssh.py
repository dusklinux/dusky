#!/usr/bin/env python3
"""Configure Windows OpenSSH, using the Windows feature or a local portable ZIP.

--source PATH supplies offline Features-on-Demand media (no Windows Update).
--openssh-zip PATH instead installs an architecture-matching Win32-OpenSSH ZIP.
Existing account passwords and public keys are retained unless explicitly changed.
"""

import argparse
import ctypes
import getpass
import os
from pathlib import Path
import subprocess
import sys
import zipfile


def ps_quote(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


def powershell(command: str) -> str:
    prefix = "$ErrorActionPreference='Stop'; [Console]::OutputEncoding=[Text.UTF8Encoding]::new($false); "
    result = subprocess.run(
        ["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", prefix + command],
        capture_output=True, text=True, encoding="utf-8", check=False,
    )
    if result.returncode:
        raise RuntimeError(result.stderr.strip() or result.stdout.strip())
    return result.stdout.strip()


def install_server(source: Path | None, archive: Path | None) -> None:
    if powershell("if (Get-Service sshd -ErrorAction SilentlyContinue) { 'Installed' }") == "Installed":
        return
    if archive is not None:
        # The ZIP must be supplied for this Windows architecture. Do not fetch
        # an old beta or guess a download/version independently of the ISO.
        program_files = Path(os.environ["ProgramFiles"])
        with zipfile.ZipFile(archive) as bundle:
            installers = [name for name in bundle.namelist() if name.endswith("/install-sshd.ps1")]
            if len(installers) != 1:
                raise RuntimeError("The OpenSSH ZIP must contain one install-sshd.ps1.")
            bundle.extractall(program_files)
            installer = program_files / installers[0]
        subprocess.run(["powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass",
                        "-File", str(installer)], cwd=installer.parent, check=True)
    else:
        if source is None:
            raise RuntimeError("OpenSSH Server is absent. Supply --source with offline Windows "
                               "Features-on-Demand media or --openssh-zip with a local package.")
        options = f" -Source {ps_quote(str(source.resolve()))} -LimitAccess"
        powershell("Add-WindowsCapability -Online -Name OpenSSH.Server~~~~0.0.1.0" + options)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, allow_abbrev=False)
    media = parser.add_mutually_exclusive_group()
    media.add_argument("--source", type=Path, help="offline Windows Features-on-Demand source")
    media.add_argument("--openssh-zip", type=Path, help="local Win32-OpenSSH ZIP for this architecture")
    parser.add_argument("--public-key", type=Path, help="append this host key for the administrator account")
    args = parser.parse_args()
    if sys.platform != "win32":
        parser.error("Run this script inside Windows 11.")
    if sys.version_info < (3, 15):
        parser.error("Python 3.15+ is required.")
    if not ctypes.windll.shell32.IsUserAnAdmin():
        parser.error("Run 00_bootstrap.ps1 or an Administrator terminal first.")
    install_server(args.source, args.openssh_zip)
    powershell(r"""
if (-not (Get-NetFirewallRule -Name 'OpenSSH-Server-In-TCP' -ErrorAction SilentlyContinue)) {
    New-NetFirewallRule -Name 'OpenSSH-Server-In-TCP' -DisplayName 'OpenSSH Server (sshd)' -Enabled True -Direction Inbound -Protocol TCP -Action Allow -LocalPort 22 | Out-Null
} else { Enable-NetFirewallRule -Name 'OpenSSH-Server-In-TCP' }
if (-not (Test-Path 'HKLM:\SOFTWARE\OpenSSH')) { $null = New-Item -Path 'HKLM:\SOFTWARE\OpenSSH' }
New-ItemProperty -Path 'HKLM:\SOFTWARE\OpenSSH' -Name DefaultShell -PropertyType String -Value (Join-Path $env:SystemRoot 'System32\WindowsPowerShell\v1.0\powershell.exe') -Force | Out-Null
Set-Service -Name sshd -StartupType Automatic
Start-Service sshd
(Get-Service sshd).WaitForStatus('Running', [TimeSpan]::FromSeconds(30))
""")
    username = os.environ["USERNAME"]
    if sys.stdin.isatty():
        while True:
            password = getpass.getpass(f"New password for {username} (Enter to keep current): ")
            if not password:
                break
            if password != getpass.getpass("Confirm password: "):
                print("Passwords differ; retry.")
                continue
            subprocess.run(["net.exe", "user", username, password], check=True)
            break
    if args.public_key is not None:
        key = args.public_key.read_text(encoding="utf-8").strip()
        if not key or "\n" in key:
            raise ValueError("Provide a single OpenSSH public key.")
        auth_keys = Path(os.environ["ProgramData"]) / "ssh" / "administrators_authorized_keys"
        auth_keys.parent.mkdir(parents=True, exist_ok=True)
        existing = auth_keys.read_text(encoding="utf-8") if auth_keys.exists() else ""
        if key not in existing.splitlines():
            auth_keys.write_text(existing.rstrip("\r\n") + ("\n" if existing else "") + key + "\n", encoding="utf-8")
        # Windows OpenSSH requires SYSTEM/Administrators ACLs on this file.
        subprocess.run(["icacls.exe", str(auth_keys), "/inheritance:r", "/grant:r",
                        "*S-1-5-18:(F)", "*S-1-5-32-544:(F)"], check=True)
    ips = powershell("(Get-NetIPAddress -AddressFamily IPv4 | Where-Object { "
                     "$_.IPAddress -ne '127.0.0.1' -and $_.AddressState -eq 'Preferred' }).IPAddress")
    print("OpenSSH service is running. Existing passwords were kept unless changed above.")
    for ip in ips.splitlines():
        print(f"  ssh {username}@{ip}")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (OSError, RuntimeError, ValueError, subprocess.CalledProcessError, zipfile.BadZipFile) as exc:
        print(f"[ERROR] {exc}", file=sys.stderr)
        raise SystemExit(1) from None
    except (EOFError, KeyboardInterrupt):
        raise SystemExit(130) from None
