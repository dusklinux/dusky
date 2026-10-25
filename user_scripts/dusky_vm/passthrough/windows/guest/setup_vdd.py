#!/usr/bin/env python3
"""Install the matching Looking Glass IDD (the filename is retained for callers).

Place looking-glass-idd-setup.exe beside this script or pass --installer PATH.
Use the installer from the same source release as the Linux client. No separate
MttVDD, devcon package, or legacy Looking Glass Host service is needed.
"""

import argparse
import ctypes
from pathlib import Path
import subprocess
import sys


IDD_STATUS = r"""
$shared = @(Get-PnpDevice -PresentOnly | Where-Object InstanceId -like 'PCI\VEN_1AF4&DEV_1110*')
if (-not $shared -or @($shared | Where-Object Status -ne 'OK').Count -gt 0) {
    'IVSHMEMNotReady'
    return
}
$devices = @(Get-PnpDevice -Class Display,HIDClass -PresentOnly | ForEach-Object {
    $ids = (Get-PnpDeviceProperty -InstanceId $_.InstanceId -KeyName 'DEVPKEY_Device_HardwareIds' -ErrorAction SilentlyContinue).Data
    if ($ids -contains 'Root\LGIdd' -or $ids -contains 'Root\LGInput') {
        [pscustomobject]@{ Device = $_; HardwareIds = $ids }
    }
})
$display = @($devices | Where-Object { $_.HardwareIds -contains 'Root\LGIdd' })
$inputDevice = @($devices | Where-Object { $_.HardwareIds -contains 'Root\LGInput' })
$service = Get-Service -Name LGIddHelper -ErrorAction SilentlyContinue
if (-not $display -or -not $inputDevice -or @($devices | Where-Object { $_.Device.Status -ne 'OK' }).Count -gt 0 -or
    -not $service -or $service.Status -ne 'Running') { 'RestartRequired' } else { 'Ready' }
"""

def find_installer(explicit: Path | None) -> Path:
    if explicit is not None:
        path = explicit.expanduser().resolve()
        if not path.is_file():
            raise FileNotFoundError(path)
        return path
    for directory in (Path(__file__).resolve().parent, Path.cwd()):
        path = directory / "looking-glass-idd-setup.exe"
        if path.is_file():
            return path
    raise FileNotFoundError(
        "Place the matching looking-glass-idd-setup.exe beside this script, "
        "or pass --installer PATH. Download the IDD and Linux client from the same build."
    )


def powershell(command: str) -> str:
    prefix = "$ErrorActionPreference='Stop'; [Console]::OutputEncoding=[Text.UTF8Encoding]::new($false); "
    result = subprocess.run(
        # An absent optional Get-Service leaves $? false even with
        # SilentlyContinue. Explicit success avoids a spurious exit 1; genuine
        # terminating errors still stop the command before exit 0.
        ["powershell.exe", "-NoProfile", "-NonInteractive", "-Command",
         prefix + "& { " + command + "\n}; exit 0"],
        capture_output=True, text=True, encoding="utf-8", check=False,
    )
    if result.returncode:
        raise RuntimeError(result.stderr.strip() or result.stdout.strip()
                           or f"PowerShell failed (exit {result.returncode}).")
    return result.stdout.strip()


def trust_installer_publisher(installer: Path) -> None:
    quoted = "'" + str(installer).replace("'", "''") + "'"
    print(powershell(r"""
$signature = Get-AuthenticodeSignature -LiteralPath """ + quoted + r"""
if ($signature.Status -ne 'Valid' -or -not $signature.SignerCertificate) {
    throw ('Cannot trust the installer publisher: ' + $signature.StatusMessage)
}
$store = [Security.Cryptography.X509Certificates.X509Store]::new('TrustedPublisher', 'LocalMachine')
$store.Open('ReadWrite')
try { $store.Add($signature.SignerCertificate) } finally { $store.Close() }
'Trusted verified installer publisher: ' + $signature.SignerCertificate.Subject
"""))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, allow_abbrev=False)
    parser.add_argument("--installer", type=Path, help="matching local Looking Glass IDD installer")
    publisher = parser.add_mutually_exclusive_group()
    publisher.add_argument("--publisher-certificate", type=Path,
                           help="explicitly trust this supplied publisher certificate for silent driver installation")
    publisher.add_argument("--trust-installer-publisher", action="store_true",
                           help="trust the installer's publisher after Windows verifies its Authenticode signature")
    args = parser.parse_args()
    if sys.platform != "win32":
        parser.error("Run this script inside Windows 11.")
    if sys.version_info < (3, 15):
        parser.error("Python 3.15+ is required.")
    if not ctypes.windll.shell32.IsUserAnAdmin():
        parser.error("Run 00_bootstrap.ps1 or an Administrator terminal first.")
    installer = find_installer(args.installer)
    if args.trust_installer_publisher:
        trust_installer_publisher(installer)
    if args.publisher_certificate is not None:
        certificate = args.publisher_certificate.expanduser().resolve(strict=True)
        quoted = "'" + str(certificate).replace("'", "''") + "'"
        powershell(f"Import-Certificate -FilePath {quoted} "
                   r"-CertStoreLocation Cert:\LocalMachine\TrustedPublisher | Out-Null")
    print(f"Installing Looking Glass IDD from {installer}")
    # NSIS customGUIInit selects the old-host disabling section only in GUI
    # mode. A silent install must stop that competing shared-memory server.
    powershell("$old=Get-Service -Name 'Looking Glass (host)' -ErrorAction SilentlyContinue; "
               "if ($old) { Stop-Service -InputObject $old -Force; "
               "Set-Service -InputObject $old -StartupType Disabled }")
    result = subprocess.run([str(installer), "/S", "/ivshmem"], check=False)
    if result.returncode not in (0, 3010):
        if result.returncode == 13:
            raise RuntimeError("IDD installation failed and requires a Windows restart before retrying (exit 13).")
        raise RuntimeError(
            f"IDD installer failed (exit {result.returncode}). Check "
            r"%SystemRoot%\inf\setupapi.dev.log. A fresh Windows installation may "
            "require publisher approval before a silent driver install; see README.md "
            "and --publisher-certificate."
        )
    # The NSIS wrapper sets its reboot flag for helper exit 12, but can itself
    # return 0. Query the exact hardware IDs from LGIddInstall.c, not names.
    status = powershell(IDD_STATUS)
    if result.returncode == 3010 or status != "Ready":
        if status == "IVSHMEMNotReady" and result.returncode != 3010:
            raise RuntimeError("The VM's IVSHMEM device/driver is not ready. Verify its domain "
                               "shared-memory device and install the bundled IVSHMEM driver.")
        print("IDD installer completed; restart Windows, then verify the Looking Glass display and helper.")
        return 3010
    print("Looking Glass display/input devices and IDD helper are ready.")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (OSError, RuntimeError) as exc:
        print(f"[ERROR] {exc}", file=sys.stderr)
        raise SystemExit(1) from None
    except (EOFError, KeyboardInterrupt):
        raise SystemExit(130) from None
