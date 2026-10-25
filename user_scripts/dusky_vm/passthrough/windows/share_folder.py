#!/usr/bin/env python3
"""Configure one Windows VirtIO-FS share; default host directory /mnt/zram1.

Host hardware changes require a powered-off VM. --guest configures the installed
Windows service through QGA after starting the VM; no downloads or reboots.
"""

import argparse
import base64
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import xml.etree.ElementTree as ET

TAG = 'dusky-host'
DEFAULT = Path('/mnt/zram1')


def virsh(*args):
    p = subprocess.run(['virsh', '-c', 'qemu:///system', *args],
                       capture_output=True, text=True, timeout=40,
                       env={**os.environ, 'LC_ALL': 'C'})
    if p.returncode:
        raise RuntimeError(p.stderr.strip() or p.stdout.strip())
    return p.stdout.strip()


def configure(root, path):
    """Change only our filesystem and the memory backing required by virtiofs."""
    devices = root.find('devices')
    if devices is None:
        raise RuntimeError('Domain has no devices element.')
    share = None
    for fs in devices.findall('filesystem'):
        driver, target = fs.find('driver'), fs.find('target')
        if target is not None and target.get('dir') == TAG and (driver is None or driver.get('type') != 'virtiofs'):
            raise RuntimeError(f'The tag {TAG} is already used by a different filesystem driver.')
        if driver is not None and driver.get('type') == 'virtiofs':
            if target is None or target.get('dir') != TAG or share is not None:
                raise RuntimeError('Another VirtIO-FS share exists. This single-drive setup leaves it unchanged.')
            share = fs
    backing = root.find('memoryBacking')
    if backing is None:
        backing = ET.SubElement(root, 'memoryBacking')
        ET.SubElement(backing, 'source', type='memfd')
    source = backing.find('source')
    if source is not None and source.get('type') not in ('memfd', 'file'):
        raise RuntimeError('Existing memory backing cannot be shared; configure it explicitly first.')
    access = backing.find('access')
    if access is None:
        access = ET.SubElement(backing, 'access')
    access.set('mode', 'shared')
    if share is None:
        share = ET.SubElement(devices, 'filesystem', type='mount', accessmode='passthrough')
        ET.SubElement(share, 'driver', type='virtiofs', queue='1024')
        ET.SubElement(share, 'source')
        ET.SubElement(share, 'target', dir=TAG)
    source = share.find('source')
    if source is None:
        raise RuntimeError('The existing managed share lacks its source directory.')
    source.attrib.clear()
    source.set('dir', str(path))
    # Both sides edit files: avoid keeping stale guest data/metadata cached.
    binary = share.find('binary')
    if binary is None:
        binary = ET.SubElement(share, 'binary')
    cache = binary.find('cache')
    if cache is None:
        cache = ET.SubElement(binary, 'cache')
    cache.set('mode', 'none')
    return root


def agent(vm, command):
    return json.loads(virsh('qemu-agent-command', vm, json.dumps(command), '--timeout', '30'))['return']


def guest_text(encoded):
    text = base64.b64decode(encoded).decode('utf-8', errors='replace')
    if text.startswith('#< CLIXML'):
        root = ET.fromstring(text.split('\n', 1)[1])
        text = '\n'.join(node.text or '' for node in root
                         if node.get('S') in ('Error', 'Warning'))
        text = text.replace('_x000D_', '\r').replace('_x000A_', '\n')
    return text.strip()


def setup_guest(vm, uid, gid, letter, installer):
    script = Path(__file__).with_name('guest') / 'setup_share.ps1'
    quote = lambda value: "'" + value.replace("'", "''") + "'"
    invocation = f"& {{\n{script.read_text()}\n}} -Owner '{uid}:{gid}' -DriveLetter '{letter}'"
    if installer:
        invocation += ' -WinFspInstaller ' + quote(installer)
    encoded = base64.b64encode(invocation.encode('utf-16le')).decode()
    pid = agent(vm, {'execute': 'guest-exec', 'arguments': {
        'path': 'powershell.exe', 'arg': ['-NoProfile', '-NonInteractive', '-ExecutionPolicy', 'Bypass',
                                       '-EncodedCommand', encoded], 'capture-output': True}})['pid']
    deadline = time.monotonic() + 300
    while time.monotonic() < deadline:
        result = agent(vm, {'execute': 'guest-exec-status', 'arguments': {'pid': pid}})
        if result.get('exited'):
            for key in ('out-data', 'err-data'):
                if result.get(key):
                    output = guest_text(result[key])
                    if output:
                        print(output)
            code = result.get('exitcode', 1)
            if code == 3010:
                raise RuntimeError('Windows requested a restart. Restart the guest, then rerun --guest.')
            if code:
                raise RuntimeError(f'Windows share setup failed (exit {code}).')
            return
        time.sleep(1)
    raise RuntimeError('Guest setup is still running after 300 seconds; inspect Windows before retrying.')


def main():
    if sys.version_info < (3, 15):
        raise RuntimeError('Python 3.15+ is required.')
    parser = argparse.ArgumentParser(description=__doc__, allow_abbrev=False)
    parser.add_argument('--vm')
    parser.add_argument('--dir', type=Path, help='host folder; default existing share or /mnt/zram1')
    parser.add_argument('--status', action='store_true', help='show configured host folder without changing it')
    parser.add_argument('--guest', action='store_true', help='configure Windows through its running QEMU guest agent')
    parser.add_argument('--drive-letter', default='Z', help='Windows drive letter for --guest (default Z)')
    parser.add_argument('--winfsp-installer', help='offline WinFsp MSI path inside Windows, for --guest')
    args = parser.parse_args()
    letter = args.drive_letter.upper().removesuffix(':')
    if len(letter) != 1 or not 'D' <= letter <= 'Z':
        parser.error('--drive-letter must be D through Z')
    if args.status and (args.dir or args.guest or args.winfsp_installer):
        parser.error('--status cannot configure the host or guest')
    if args.winfsp_installer and not args.guest:
        parser.error('--winfsp-installer requires --guest')
    spec = importlib.util.spec_from_file_location('dusky_win', Path(__file__).with_name('50_win.py'))
    manager = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(manager)
    vm = manager.resolve_vm(args.vm)
    xml = virsh('dumpxml', vm, '--inactive')
    root = ET.fromstring(xml)
    share = root.find(f"./devices/filesystem/target[@dir='{TAG}']/..")
    current = share.find('source').get('dir') if share is not None and share.find('source') is not None else None
    if args.status:
        print(f'{vm}: {current or "no Dusky shared folder configured"}')
        return
    if args.guest:
        if args.dir:
            parser.error('Configure --dir while powered off, then use --guest after starting Windows.')
        if not current:
            raise RuntimeError('Configure the host share first, then start Windows and run --guest.')
        if virsh('domstate', vm) != 'running':
            raise RuntimeError('Start Windows before running --guest.')
        uid, gid = manager.get_caller_identity()[1:]
        setup_guest(vm, uid, gid, letter, args.winfsp_installer)
        print(f'{current} → Windows {letter}:')
        return
    path = (args.dir or Path(current or DEFAULT)).expanduser().resolve()
    if not path.is_dir():
        raise RuntimeError(f'Host directory does not exist: {path}. Mount/create it first, or pass --dir PATH.')
    # Do not turn a missing RAM mount into an accidental root-filesystem share.
    if path == DEFAULT and not path.is_mount():
        raise RuntimeError(f'Mount {DEFAULT} before configuring its share.')
    before = ET.tostring(root)
    configure(root, path)
    if ET.tostring(root) == before:
        print(f'Already configured: {path} → {TAG}; no XML change.')
        return
    if virsh('domstate', vm) != 'shut off':
        raise RuntimeError('Shut Windows down before changing its shared-folder device.')
    if not Path('/usr/share/qemu/vhost-user').is_dir() or not any(
            'virtiofsd' in p.read_text() for p in Path('/usr/share/qemu/vhost-user').glob('*.json')):
        raise RuntimeError('Install the Arch virtiofsd package before configuring the share.')
    home = manager.get_caller_identity()[0]
    backup = home / '.local/state/dusky/vm-share' / virsh('domuuid', vm)
    backup.mkdir(parents=True, exist_ok=True)
    original = backup / 'before-share.xml'
    if not original.exists():
        original.write_text(xml + '\n')
    with tempfile.NamedTemporaryFile(mode='w', suffix='.xml') as f:
        f.write(ET.tostring(root, encoding='unicode'))
        f.flush()
        virsh('define', f.name, '--validate')
    print(f'Configured {path} → {TAG}. Start Windows, then run share --guest --vm {vm}.')


if __name__ == '__main__':
    try:
        main()
    except (OSError, ValueError, KeyError, RuntimeError, subprocess.TimeoutExpired, ET.ParseError) as exc:
        sys.exit(f'Shared-folder setup failed: {exc}')
