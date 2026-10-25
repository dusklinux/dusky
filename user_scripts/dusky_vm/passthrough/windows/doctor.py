#!/usr/bin/env python3
"""Diagnose the selected Windows VM; --repair fixes verifiable configuration drift.

Repairs preserve disks, CPU/GPU choices and active shared-memory mappings.
No downloads, driver replacement, GPU rebinding or automatic reboot is performed.
Exit 0: selected checks passed; 1: unresolved faults; 2: checks pending.
"""

import argparse
import base64
from contextlib import redirect_stdout
import grp
import importlib.util
import json
import os
from pathlib import Path
import platform
import pwd
import re
import shutil
import stat
import subprocess
import sys
import tempfile
import time
import xml.etree.ElementTree as ET


HERE = Path(__file__).resolve().parent
WINDOWS_NS = "urn:dusky:vm:windows:1"


def module(name, relative):
    spec = importlib.util.spec_from_file_location(name, HERE / relative)
    result = importlib.util.module_from_spec(spec)
    sys.modules[name] = result
    spec.loader.exec_module(result)
    return result


def command(argv, timeout=20):
    result = subprocess.run(argv, capture_output=True, text=True, timeout=timeout,
                            env={**os.environ, "LC_ALL": "C"}, stdin=subprocess.DEVNULL)
    if result.returncode:
        raise RuntimeError(result.stderr.strip() or result.stdout.strip() or f"{argv[0]} exited {result.returncode}")
    return result.stdout.strip()


def virsh(*args):
    return command(["virsh", "-c", "qemu:///system", *args])


def operator():
    return pwd.getpwnam(os.environ["SUDO_USER"]) if os.geteuid() == 0 and os.environ.get("SUDO_USER") else pwd.getpwuid(os.getuid())


def access_method(root, override="auto"):
    if override != "auto":
        return override
    selected = root.find(f"./metadata/{{{WINDOWS_NS}}}windows")
    if selected is not None:
        if selected.get("access") not in ("both", "looking-glass", "rdp", "spice"):
            raise ValueError("Unknown saved Windows access choice; select --access explicitly or update the domain metadata.")
        return selected.get("access")
    # Existing domains have no recorded RDP consent. Infer only their device.
    return "looking-glass" if root.find("./devices/shmem[@name='looking-glass']") is not None else "spice"


def shm_size(root):
    size = root.find("./devices/shmem[@name='looking-glass']/size")
    if size is None:
        return 0
    units = {"B": 1, "bytes": 1, "K": 1024, "KiB": 1024, "M": 1 << 20,
             "MiB": 1 << 20, "G": 1 << 30, "GiB": 1 << 30}
    if size.get("unit", "KiB") not in units:
        raise ValueError("Unsupported shared-memory size unit; use Phase 25 to normalize it.")
    value = int(size.text) * units[size.get("unit", "KiB")]
    if value < 1 << 20 or value & (value - 1):
        raise ValueError("The domain's shared-memory region must be a power of two, at least 1 MiB.")
    return value


def channel(root, name):
    return root.find(f"./devices/channel/target[@name='{name}']") is not None


def managed_devices(root):
    shmem = root.find("./devices/shmem[@name='looking-glass']/model")
    return (shm_size(root), None if shmem is None else shmem.get("type"),
            channel(root, "org.qemu.guest_agent.0"), channel(root, "com.redhat.spice.0"))


def repair_channels(root):
    devices = root.find("devices")
    if devices is None:
        raise ValueError("Domain has no devices element.")
    changed = False
    wanted = [("org.qemu.guest_agent.0", "unix")]
    if devices.find("graphics[@type='spice']") is not None:
        wanted.append(("com.redhat.spice.0", "spicevmc"))
    for name, kind in wanted:
        if not channel(root, name):
            node = ET.SubElement(devices, "channel", type=kind)
            ET.SubElement(node, "target", type="virtio", name=name)
            changed = True
    return changed


def guest_agent(vm, request):
    result = json.loads(virsh("qemu-agent-command", vm, "--timeout", "5", json.dumps(request)))
    if "error" in result:
        raise RuntimeError(str(result["error"]))
    return result["return"]


def guest_script(vm, script, timeout=90):
    prefix = "$ErrorActionPreference='Stop'; $ProgressPreference='SilentlyContinue'; [Console]::OutputEncoding=[Text.UTF8Encoding]::new($false); "
    encoded = base64.b64encode((prefix + script).encode("utf-16le")).decode()
    pid = guest_agent(vm, {"execute": "guest-exec", "arguments": {
        "path": "powershell.exe", "arg": ["-NoProfile", "-NonInteractive", "-EncodedCommand", encoded],
        "capture-output": True}})["pid"]
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        result = guest_agent(vm, {"execute": "guest-exec-status", "arguments": {"pid": pid}})
        if result.get("exited"):
            output = base64.b64decode(result.get("out-data", "")).decode("utf-8", errors="replace")
            error = base64.b64decode(result.get("err-data", "")).decode("utf-8", errors="replace")
            if result.get("exitcode") != 0 or result.get("out-truncated"):
                raise RuntimeError(error.strip() or output.strip() or "Guest health command failed")
            return json.loads(output)
        time.sleep(0.25)
    raise TimeoutError("Guest health check timed out; no service/driver restart was forced.")


def guest_checks(vm, access, repair, ssh=False, spice=True, respect_choices=True):
    idd = module("doctor_guest_idd", "guest/setup_vdd.py")
    script = r"""
$checks = [Collections.Generic.List[object]]::new()
function Check([string]$Name,[bool]$Ok,[string]$Detail) {
    $checks.Add([pscustomobject]@{name=$Name;status=$(if($Ok){'ok'}else{'issue'});detail=$Detail})
}
function Service([string]$Name) {
    $service=Get-Service -Name $Name -ErrorAction SilentlyContinue
    try {
    if ($service -and $repair) {
        if ($service.StartType -eq 'Disabled') { Set-Service -Name $Name -StartupType Automatic }
        if ($service.Status -ne 'Running') { Start-Service -Name $Name }
        $service.WaitForStatus('Running',[TimeSpan]::FromSeconds(20))
        $service.Refresh()
    }
    Check ('guest.service.'+$Name) ([bool]($service -and $service.Status -eq 'Running')) 'Install the selected guest component if absent; --repair starts installed services.'
    } catch { Check ('guest.service.'+$Name) $false $_.Exception.Message }
}
$repair=__REPAIR__; $lg=__LG__; $rdp=__RDP__; $ssh=__SSH__
$spice=__SPICE__
$choicePath=Join-Path $env:ProgramData 'DuskyVM\choices.json'
if (__RESPECT__ -and (Test-Path -LiteralPath $choicePath)) {
    $choice=Get-Content -LiteralPath $choicePath -Raw | ConvertFrom-Json
    if ($choice.schema -ne 1) {throw 'Unsupported guest choices; rerun the bootstrapper to select components.'}
    foreach ($key in @('lookingGlass','rdp','ssh','spice')) {
        if ($choice.$key -isnot [bool]) {throw 'Invalid saved guest choices; rerun the bootstrapper.'}
    }
    $lg=$choice.lookingGlass; $rdp=$choice.rdp; $ssh=$ssh -or $choice.ssh; $spice=$spice -and $choice.spice
    Check 'guest.saved-choices' $true 'Guest repairs follow the component choices saved by Windows setup. --access explicitly overrides the access scope.'
}
if ($spice) {
    $agent=Get-CimInstance Win32_Service | Where-Object PathName -match '(?i)[\\/]vdservice\.exe(?:"|\s|$)' | Select-Object -First 1
    if ($agent) { Service $agent.Name } else {
        $checks.Add([pscustomobject]@{name='guest.spice-agent';status='optional';detail='SPICE clipboard fallback is absent. Install VirtIO-Win guest tools if wanted; IDD/RDP have their own clipboard paths.'})
    }
}
if ($lg) {
    Service 'LGIddHelper'
    $ready = & { __IDD_STATUS__ }
    Check 'guest.idd-devices' ($ready -eq 'Ready') ('IDD status: '+$ready+'. Use the matching IDD installer when drivers are missing; inspect SetupAPI/IDD logs if a restart did not resolve it.')
    $old=Get-Service -Name 'Looking Glass (host)' -ErrorAction SilentlyContinue
    if ($old -and $repair) { Stop-Service -InputObject $old -Force; Set-Service -InputObject $old -StartupType Disabled; $old.Refresh() }
    Check 'guest.legacy-host' ([bool](-not $old -or $old.Status -ne 'Running')) 'The legacy Host must not compete with the IDD for shared memory.'
    $ui=@(Get-Process LGIddHelper -ErrorAction SilentlyContinue | Where-Object SessionId -gt 0)
    if (-not $ui) { $checks.Add([pscustomobject]@{name='guest.desktop-session';status='pending';detail='Sign in through the SPICE console once so the IDD user helper can select the monitor; then relaunch Looking Glass.'}) }
}
if ($rdp) {
    $edition=(Get-ItemProperty 'HKLM:\SOFTWARE\Microsoft\Windows NT\CurrentVersion').EditionID
    $supported=$edition -match '^(Professional|Enterprise|Education|IoTEnterprise)'
    Check 'guest.rdp-edition' $supported $edition
    if ($supported) {
        if ($repair) {
            $settings=Get-CimInstance -Namespace 'root/cimv2/TerminalServices' -ClassName Win32_TerminalServiceSetting
            $result=Invoke-CimMethod -InputObject $settings -MethodName SetAllowTSConnections -Arguments @{AllowTSConnections=[uint32]1;ModifyFirewallException=[uint32]1}
            if ($result.ReturnValue -ne 0) {throw ('RDP management API failed: '+$result.ReturnValue)}
        }
        Service 'TermService'
        $enabled=(Get-ItemProperty 'HKLM:\SYSTEM\CurrentControlSet\Control\Terminal Server').fDenyTSConnections -eq 0
        Check 'guest.rdp-enabled' $enabled 'Enable Remote Desktop or rerun with --repair. NLA/password policy is retained.'
        $rules=@(Get-NetFirewallRule -Name 'RemoteDesktop-UserMode-In-TCP' -ErrorAction SilentlyContinue)
        Check 'guest.rdp-firewall' ([bool]($rules | Where-Object Enabled -eq 'True')) 'The selected RDP TCP firewall exception must be enabled.'
    }
}
if ($ssh) { Service 'sshd' }
ConvertTo-Json -InputObject @($checks.ToArray()) -Compress -Depth 4
"""
    for key, value in {"REPAIR": repair, "LG": access in ("both", "looking-glass"),
                       "RDP": access in ("both", "rdp"), "SSH": ssh, "SPICE": spice, "RESPECT": respect_choices}.items():
        script = script.replace("__" + key + "__", "$true" if value else "$false")
    return guest_script(vm, script.replace("__IDD_STATUS__", idd.IDD_STATUS))


def inspect(vm, access, repair=False, guest=False, ssh=False):
    checks = []
    def add(name, status, detail):
        checks.append({"name": name, "status": status, "detail": detail})
    human = operator()
    if human.pw_uid == 0:
        raise ValueError("Run as the desktop user; --repair handles sudo and retains that user's identity.")
    release = platform.release()
    match = re.match(r"(\d+)\.(\d+)", release)
    kernel_ok = match and tuple(map(int, match.groups())) >= (7, 3) and not (match.groups() == ("7", "3") and "-rc" in release)
    add("host.kernel", "ok" if kernel_ok else "issue", release + "; final supported ISO baseline is kernel 7.3+.")
    systemd = command(["systemctl", "--version"]).splitlines()[0]
    version = re.search(r"systemd (\d+)", systemd)
    add("host.systemd", "ok" if version and int(version[1]) >= 262 else "issue", systemd)
    try:
        command(["systemctl", "is-active", "virtqemud.socket"])
    except RuntimeError:
        if repair:
            command(["systemctl", "enable", "--now", "virtqemud.socket"])
            command(["systemctl", "is-active", "virtqemud.socket"])
        else:
            add("host.libvirt", "issue", "virtqemud.socket is inactive; --repair can enable/start the installed socket.")
    if not any(check["name"] == "host.libvirt" for check in checks):
        add("host.libvirt", "ok", "virtqemud.socket is active.")
    respect_choices = access == "auto"
    root = ET.fromstring(virsh("dumpxml", "--inactive", vm))
    access = access_method(root, access)
    want_lg = access in ("looking-glass", "both")
    if want_lg or access == "rdp":
        add("host.wayland-session", "ok" if os.environ.get("WAYLAND_DISPLAY") else "pending", "Client UI tests require the desktop user's Wayland session.")
    phase = module("doctor_phase25", "25_looking_glass.py")
    config = module("doctor_config", "60_configure_client_ini.py")
    changed = repair_channels(root) if repair else False
    if want_lg and repair and root.find("./devices/shmem[@name='looking-glass']") is None:
        mib = int(phase.state_load().get("shm_mib") or 64)
        if mib <= 0 or mib & (mib - 1):
            raise ValueError("Invalid Phase 25 memory size; choose a power-of-two size with Phase 25.")
        phase.apply_shmem(root, mib)
        changed = True
    if changed:
        with tempfile.NamedTemporaryFile(mode="w", suffix=".xml", encoding="utf-8") as handle:
            handle.write(ET.tostring(root, encoding="unicode")); handle.flush()
            virsh("define", handle.name, "--validate")
        root = ET.fromstring(virsh("dumpxml", "--inactive", vm))
    add("domain.guest-agent-channel", "ok" if channel(root, "org.qemu.guest_agent.0") else "issue", "QGA channel in persistent domain XML.")
    if root.find("./devices/graphics[@type='spice']") is not None:
        add("domain.spice-agent-channel", "ok" if channel(root, "com.redhat.spice.0") else "issue", "SPICE guest-agent channel in persistent domain XML.")
    running = virsh("domstate", vm) == "running"
    active = ET.fromstring(virsh("dumpxml", vm)) if running else root
    pending = running and managed_devices(active) != managed_devices(root)
    if pending:
        add("domain.hardware-pending", "pending", "Persistent device changes require a guest power-off/start; an in-guest reboot is insufficient.")
    if want_lg:
        for other in virsh("list", "--name").splitlines():
            if other != vm and ET.fromstring(virsh("dumpxml", other)).find("./devices/shmem[@name='looking-glass']") is not None:
                add("domain.mapping-conflict", "issue", f"'{other}' uses the same mapping. Stop it before using '{vm}'; canonical mapping supports one guest at a time.")
        size = shm_size(root)
        model = root.find("./devices/shmem[@name='looking-glass']/model")
        valid = bool(size and model is not None and model.get("type") == "ivshmem-plain")
        add("domain.shared-memory", "ok" if valid else "issue", "Run Phase 25 --domain VM to add/normalize the selected Looking Glass device.")
        if valid:
            current = phase.SHM_PATH.stat() if phase.SHM_PATH.exists() else None
            kvm = grp.getgrnam("kvm").gr_gid
            def mapping_ok(current):
                return bool(current and not phase.SHM_PATH.is_symlink() and stat.S_ISREG(current.st_mode) and current.st_size == size and stat.S_IMODE(current.st_mode) == 0o660 and current.st_uid == human.pw_uid and current.st_gid == kvm)
            correct = mapping_ok(current)
            if not correct and repair and not pending:
                try:
                    with redirect_stdout(sys.stderr):
                        phase.stage_shm(human, size, False)
                except SystemExit:
                    add("host.shm", "issue", "Mapping recreation/resize was refused while a VM uses it; power off its users and rerun --repair.")
                else:
                    correct = mapping_ok(phase.SHM_PATH.stat())
            add("host.shm-permissions-size", "ok" if correct else "issue", "Shared-memory owner, group, mode and capacity must match; an active mapping is never replaced.")
            if repair:
                with redirect_stdout(sys.stderr):
                    phase.configure_tmpfiles(human)
            expected = f"f /dev/shm/looking-glass 0660 {human.pw_name} kvm - -"
            add("host.tmpfiles", "ok" if phase.TMPFILES.exists() and expected in phase.TMPFILES.read_text(encoding="utf-8") else "issue", "Boot-time shared-memory staging; --repair regenerates its managed rule.")
        add("host.lg-client", "ok" if phase.current_client_available(human) else "issue", "A matching current client with Wayland/SPICE support must be installed; no downloads are attempted.")
        config_home = Path(os.environ.get("XDG_CONFIG_HOME") or Path(human.pw_dir) / ".config")
        if not config_home.is_absolute():
            config_home = Path(human.pw_dir) / ".config"
        path = config_home / "looking-glass/client.ini"
        old = path.read_text(encoding="utf-8") if path.exists() else ""
        new = config.merge_config(old, str(phase.SHM_PATH))
        if old != new and repair:
            config.atomic_write(path, new, human.pw_uid, human.pw_gid)
            old = path.read_text(encoding="utf-8")
        add("host.client-config", "ok" if old == new else "issue", str(path) + "; --repair merges managed keys while retaining other options.")
    if access in ("both", "rdp"):
        rdp = module("doctor_rdp", "55_rdp.py")
        try:
            path = rdp.find_freerdp_client()
            add("host.freerdp", "ok", path)
        except RuntimeError as exc:
            add("host.freerdp", "issue", str(exc))
    if guest:
        if not running or pending:
            add("guest.health", "pending", "Start/power-cycle the VM, then rerun --guest. The doctor never reboots it automatically.")
        else:
            try:
                guest_agent(vm, {"execute": "guest-ping"})
                add("guest.qga", "ok", "QEMU guest-agent round trip succeeded.")
                checks.extend(guest_checks(vm, access, repair, ssh, active.find("./devices/graphics[@type='spice']") is not None, respect_choices))
            except (RuntimeError, ValueError, TimeoutError) as exc:
                add("guest.health", "issue", str(exc) + "; use the SPICE console to install/start VirtIO-Win guest tools if QGA is unavailable.")
    return {"vm": vm, "access": access, "scope": "host+guest" if guest else "host", "repair_requested": repair, "checks": checks}


def exit_code(report):
    states = {check["status"] for check in report["checks"]}
    return 1 if "issue" in states else 2 if "pending" in states else 0


def main():
    parser = argparse.ArgumentParser(description=__doc__, allow_abbrev=False)
    parser.add_argument("--vm")
    parser.add_argument("--access", choices=["auto", "both", "looking-glass", "rdp", "spice"], default="auto")
    parser.add_argument("--guest", action="store_true", help="include Windows checks through QGA")
    parser.add_argument("--ssh", action="store_true", help="also check/repair the installed SSH service")
    parser.add_argument("--repair", action="store_true", help="repair known drift, then verify")
    parser.add_argument("--json", action="store_true", help="emit a machine-readable report")
    args = parser.parse_args()
    if sys.version_info < (3, 15):
        parser.error("Python 3.15+ is required.")
    if args.repair and os.geteuid() != 0:
        os.execvp("sudo", ["sudo", "--preserve-env=XDG_CONFIG_HOME,WAYLAND_DISPLAY,XDG_RUNTIME_DIR", "--", sys.executable, str(Path(__file__).resolve()), *sys.argv[1:]])
    if args.ssh and not args.guest:
        parser.error("--ssh requires --guest")
    if args.repair:
        try:
            command(["systemctl", "is-active", "virtqemud.socket"])
        except RuntimeError:
            command(["systemctl", "enable", "--now", "virtqemud.socket"])
    names = virsh("list", "--all", "--name").splitlines()
    vm = args.vm
    if not vm:
        if len(names) == 1:
            vm = names[0]
        elif sys.stdin.isatty() and names:
            for index, name in enumerate(names, 1):
                print(f"{index}. {name}")
            while True:
                try:
                    index = int(input("VM number [1]: ").strip() or "1")
                    if 1 <= index <= len(names):
                        vm = names[index - 1]
                        break
                except ValueError:
                    pass
                print(f"Enter a number from 1 to {len(names)}.")
        else:
            parser.error("Specify --vm when more than one VM exists (or no VM is defined).")
    if vm not in names:
        parser.error("Selected VM does not exist.")
    report = inspect(vm, args.access, args.repair, args.guest, args.ssh)
    if args.json:
        print(json.dumps(report, indent=2))
    else:
        print(f"VM: {vm}; access: {report['access']}; scope: {report['scope']}")
        for check in report["checks"]:
            print(f"[{check['status'].upper()}] {check['name']}: {check['detail']}")
        print("Pixel output, credentials, physical audio and GPU performance require their own live tests.")
    return exit_code(report)


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (OSError, RuntimeError, ValueError, KeyError, subprocess.TimeoutExpired) as exc:
        print(f"Doctor failed: {exc}", file=sys.stderr)
        raise SystemExit(1) from None
