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
        ["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", prefix + command],
        capture_output=True, text=True, encoding="utf-8", check=False,
    )
    if result.returncode:
        raise RuntimeError(result.stderr.strip() or result.stdout.strip())
    return result.stdout.strip()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, allow_abbrev=False)
    parser.add_argument("--installer", type=Path, help="matching local Looking Glass IDD installer")
    args = parser.parse_args()
    if sys.platform != "win32":
        parser.error("Run this script inside Windows 11.")
    if sys.version_info < (3, 15):
        parser.error("Python 3.15+ is required.")
    if not ctypes.windll.shell32.IsUserAnAdmin():
        parser.error("Run 00_bootstrap.ps1 or an Administrator terminal first.")
    installer = find_installer(args.installer)
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
        raise RuntimeError(f"IDD installer failed (exit {result.returncode}).")
    # The NSIS wrapper sets its reboot flag for helper exit 12, but can itself
    # return 0. Query the exact hardware IDs from LGIddInstall.c, not names.
    status = powershell(r"""
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
""")
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
