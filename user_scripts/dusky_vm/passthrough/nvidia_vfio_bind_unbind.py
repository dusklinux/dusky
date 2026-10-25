#!/usr/bin/env python3
"""Stage a GPU slot for VFIO or return it to its host drivers on the next boot.

Arch, Python 3.15+, kernel 7.3+, systemd 262, systemd-boot and mkinitcpio.
The historical filename is retained; selection and drivers are vendor independent.
No live GPU unbinding, PCI removal, firmware power gating or automatic reboot.
Keep user_scripts/gpu/gpu_disable_toggle.py alongside this tree: its PCI,
boot-entry, durable-write and mkinitcpio inspection helpers are shared here.
"""

import argparse
import fcntl
import importlib.util
import json
import os
from pathlib import Path
import re
import shlex
import subprocess
import sys
import xml.etree.ElementTree as ET

if sys.version_info < (3, 15):
    sys.exit('Python 3.15+ is required.')

# Load the supplied companion as a library; its main/elevation never runs.
COMMON_PATH = Path(__file__).resolve().parents[2] / 'gpu/gpu_disable_toggle.py'
if not COMMON_PATH.is_file():
    sys.exit('Keep user_scripts/gpu/gpu_disable_toggle.py alongside this script tree; shared GPU helpers are missing.')
_spec = importlib.util.spec_from_file_location('dusky_gpu_common', COMMON_PATH)
common = importlib.util.module_from_spec(_spec)
sys.modules[_spec.name] = common
_spec.loader.exec_module(common)

STATE = Path('/var/lib/arsonix/vfio-toggle.json')
PHASE_STATE = Path('/var/lib/arsonix/state.json')
MODPROBE = Path('/etc/modprobe.d/arsonix-vfio.conf')
MODULES = Path('/etc/mkinitcpio.conf.d/99-arsonix-vfio.conf')
CMDLINE_FRAGMENT = Path('/etc/cmdline.d/99-arsonix-vfio.conf')
KEYS = (*common.MANAGED_KEYS, 'modprobe.blacklist')
MARKER = '# Managed by Dusky VFIO toggle.'


def fail(message):
    raise RuntimeError(message)


def checked(args, **kwargs):
    result = common.run(args, **kwargs)
    if result.returncode:
        fail(f"{' '.join(args)}: {result.stderr.strip() or result.stdout.strip()}")
    return result.stdout


def load_json(path):
    try:
        data = json.loads(path.read_text(encoding='utf-8'))
    except FileNotFoundError:
        return {}
    if not isinstance(data, dict):
        fail(f'{path} must contain an object.')
    return data


def saved_state():
    data = load_json(STATE)
    if data and (data.get('schema') != 1 or data.get('mode') not in ('bind', 'unbind')
                 or not isinstance(data.get('pending'), bool)):
        fail('Unsupported or incomplete VFIO state. Restore the saved config before selecting another GPU.')
    if data and (not isinstance(data.get('host_options'), dict) or not isinstance(data.get('host_files'), dict)
                 or not isinstance(data.get('functions'), list) or not data['functions']
                 or not all(isinstance(r, dict) and isinstance(r.get('addr'), str) and isinstance(r.get('ids'), str) for r in data['functions'])):
        fail('Malformed VFIO recovery state; the recorded claim/baseline is unusable.')
    if data and (not all(k in KEYS and isinstance(v, str) for k, v in data['host_options'].items())
                 or set(data['host_files']) != {str(MODPROBE), str(MODULES)}
                 or not all(isinstance(v, (str, type(None))) for v in data['host_files'].values())
                 or not isinstance(data.get('boot_id'), str) or not data['boot_id']
                 or not isinstance(data.get('slots'), list) or not data['slots']
                 or not all(isinstance(slot, str) and re.fullmatch(r'[0-9a-f]{4}:[0-9a-f]{2}:[0-9a-f]{2}', slot) for slot in data['slots'])
                 or not all(re.fullmatch(r'[0-9a-f]{4}:[0-9a-f]{2}:[0-9a-f]{2}\.[0-7]', r['addr'])
                            and re.fullmatch(r'[0-9a-f]{4}:[0-9a-f]{4}', r['ids']) for r in data['functions'])):
        fail('Malformed VFIO baseline or hardware identifiers. Preserve the journal and restore its known-good backup.')
    if data and (not isinstance(data.get('staged_files'), dict)
                 or not {str(MODPROBE), str(MODULES)} < data['staged_files'].keys()
                 or not all(isinstance(k, str) and isinstance(v, (str, type(None))) for k, v in data['staged_files'].items())):
        fail('The VFIO journal lacks its complete staged-file inventory; configuration verification is unavailable.')
    return data


def save(data):
    common.atomic_write(STATE, json.dumps(data, indent=2, sort_keys=True) + '\n')


def parameters(text):
    result = {}
    for token in common.cmdline_tokens(text):
        if token == '--':
            break
        key, sep, value = token.partition('=')
        key = key.replace('vfio_pci.', 'vfio-pci.')
        if key in KEYS:
            if key in result:
                fail(f'Duplicate managed kernel option: {key}. Consolidate it before proceeding.')
            result[key] = value if sep else ''
    return result


def merge_options(text, desired):
    result, tail = [], []
    after = False
    for token in common.cmdline_tokens(text):
        if after:
            tail.append(token)
        elif token == '--':
            after = True
        elif token.split('=', 1)[0].replace('vfio_pci.', 'vfio-pci.') not in KEYS:
            result.append(token)
    result.extend(f'{key}={desired[key]}' for key in KEYS if key in desired)
    if after:
        result.extend(['--', *tail])
    return ' '.join(result)


def remove_claim(options, ids, drivers):
    result = dict(options)
    for key, removed in (('vfio-pci.ids', ids), ('module_blacklist', drivers), ('modprobe.blacklist', drivers)):
        if key in result:
            values = set(result[key].strip('"').split(',')) - set(removed) - {''}
            if values:
                result[key] = ','.join(sorted(values))
            else:
                result.pop(key)
    return result


def claim_for(devices, args, state):
    saved = state.get('functions') or []
    if saved:
        if args.slot and common.normalize_slot(args.slot, state['slots']) is None:
            fail('A different slot is recorded. Finish --unbind before changing the selection.')
        live = {d.addr: d for d in devices}
        functions = []
        for record in saved:
            current = live.get(record['addr'])
            if current is None or current.ids != record['ids']:
                fail(f"Recorded device {record['addr']} is absent or changed. Resolve GPU-disable/firmware settings or restore the original hardware first.")
            functions.append(current)
        if {d.addr for d in devices if d.slot in state['slots']} != {d.addr for d in functions}:
            fail('The selected slot has changed functions; do not reuse its previous claim.')
        return common.Claim(functions)
    phase = load_json(PHASE_STATE)
    slot = args.slot or phase.get('vfio_slot')
    if slot:
        normalized = common.normalize_slot(slot, common.gpu_slots(devices))
        if normalized is None:
            fail(f'GPU slot {slot} was not found.')
        return common.Claim([d for d in devices if d.slot == normalized])
    selection = argparse.Namespace(slot=None, all=False, auto=not sys.stdin.isatty())
    return common.select_claim(devices, common.gpu_slots(devices), selection)


def check_groups(claim, *, require):
    selected = set(claim.addrs)
    for dev in claim.functions:
        group = common.SYS_PCI / dev.addr / 'iommu_group'
        if not group.is_dir():
            if require:
                fail(f'{dev.addr} has no IOMMU group. Enable VT-d/AMD-Vi in firmware and boot with IOMMU enabled first.')
            print(f'{dev.addr}: no live IOMMU group yet; reboot and run --status before assigning it.')
            continue
        for member in (group / 'devices').iterdir():
            klass = common._read(member / 'class').removeprefix('0x')
            if member.name not in selected and not klass.startswith('0604'):
                fail(f'IOMMU group {group.resolve().name} also contains {member.name}; select a separately isolated GPU. No ACS override is added.')


def active_guest_claims(claim):
    if not common.shutil.which('virsh'):
        return
    names = checked(['virsh', '-c', 'qemu:///system', 'list', '--name']).splitlines()
    for name in names:
        if not name.strip():
            continue
        root = ET.fromstring(checked(['virsh', '-c', 'qemu:///system', 'dumpxml', name]))
        for address in root.findall('./devices/hostdev/source/address'):
            if address.get('domain') is None:
                continue
            bdf = f"{int(address.get('domain'), 16):04x}:{int(address.get('bus'), 16):02x}:{int(address.get('slot'), 16):02x}.{int(address.get('function'), 16):x}"
            if bdf in claim.addrs:
                fail(f'Running VM {name} uses {bdf}. Shut that guest down before changing its boot configuration.')


def foreign_config_check():
    disabled = common.state_load()
    if disabled.get('action') == 'disabled' or disabled.get('pending_action') or any(
            p.exists() for p in (common.UDEV_RULE, common.MODPROBE_FILE, common.MKINITCPIO_DROPIN, common.ASUS_TMPFILES)):
        fail('GPU-disable configuration is active. Run gpu_disable_toggle.py --enable and complete its recovery before using VFIO.')
    seen = set()
    for directory in ('/etc/modprobe.d', '/run/modprobe.d', '/usr/local/lib/modprobe.d', '/usr/lib/modprobe.d'):
        for path in sorted(Path(directory).glob('*.conf')):
            if path.name in seen:
                continue
            seen.add(path.name)
            if path == MODPROBE:
                continue
            text = path.read_text(encoding='utf-8').replace('\\\n', '')
            if re.search(r'^\s*options\s+vfio[-_]pci\b.*\bids=', text, re.MULTILINE):
                fail(f'{path} also sets vfio-pci ids. Consolidate it into the Phase 3 configuration first.')


def host_modprobe(text, ids, drivers):
    """Adopt a Phase 3 file, retaining rules for other VFIO devices."""
    output = []
    has_other_ids = False
    for line in text.splitlines():
        if re.prefixmatch(r'\s*options\s+vfio[-_]pci\b', line):
            tokens = shlex.split(line, comments=True)
            kept = []
            for token in tokens[2:]:
                if token.startswith('ids='):
                    values = set(token[4:].split(',')) - set(ids) - {''}
                    if values:
                        has_other_ids = True
                        kept.append('ids=' + ','.join(sorted(values)))
                else:
                    kept.append(token)
            if kept:
                output.append('options vfio-pci ' + ' '.join(kept))
        elif re.prefixmatch(r'\s*blacklist\s+', line):
            tokens = shlex.split(line, comments=True)
            if len(tokens) < 2 or tokens[1].replace('-', '_') not in drivers:
                output.append(line)
        else:
            output.append(line)
    if not has_other_ids:
        output = [line for line in output if not re.prefixmatch(r'\s*softdep\s+.*\bpre:\s+vfio[-_]pci\s*$', line)]
    return '\n'.join(output).rstrip() + '\n' if any(line.strip() and not line.lstrip().startswith('#') for line in output) else None


def module_payload():
    return (MARKER + '\nMODULES=(vfio_pci "${MODULES[@]}")\n'
            'if [[ ! " ${HOOKS[*]} " == *" modconf "* ]]; then HOOKS+=(modconf); fi\n')


def validate_modules(payload):
    modules, _, hooks = common.effective_config(managed_payload=payload, managed_dropin=MODULES)
    if not modules or modules[0].replace('-', '_') != 'vfio_pci' or 'modconf' not in hooks:
        fail('Later mkinitcpio configuration overrides the early VFIO modules or modconf hook.')
    if 'kms' in hooks and hooks.index('modconf') > hooks.index('kms'):
        fail('modconf must precede kms in the effective mkinitcpio HOOKS.')


def boot_payload(entry, desired):
    if entry.kind == 'type1':
        text = entry.path.read_text(encoding='utf-8')
        result, added = [], False
        for line in text.splitlines(keepends=True):
            if re.prefixmatch(r'[ \t]*options(?:[ \t]|$)', line):
                if not added:
                    result.append('options ' + merge_options(common.read_target_options(entry), desired) + '\n')
                    added = True
            else:
                result.append(line)
        if not added:
            result.append('options ' + merge_options('', desired) + '\n')
        return {str(entry.path): ''.join(result)}
    base = next((p for p in (common.KERNEL_CMDLINE, common.VENDOR_CMDLINE) if p.is_file()), None)
    foreign = [p for p in common.CMDLINE_D.glob('*.conf') if p != CMDLINE_FRAGMENT]
    if base is None and foreign:
        # Fragments already own persistent root/options; own only our fragment.
        payload = ' '.join(f'{key}={desired[key]}' for key in KEYS if key in desired)
        return {str(CMDLINE_FRAGMENT): payload + '\n' if payload else None}
    seed = base.read_text(encoding='utf-8') if base else entry.options
    if not seed:
        seed = ' '.join(t for t in common.cmdline_tokens(common._read(Path('/proc/cmdline')))
                        if t not in common.VOLATILE_TOKENS and not t.startswith(common.VOLATILE_PREFIXES))
    return {str(common.KERNEL_CMDLINE): merge_options(seed, desired) + '\n', str(CMDLINE_FRAGMENT): None}


def plan(args, state):
    if state.get('mode') == 'unbind' and not state.get('pending') and args.slot and common.normalize_slot(args.slot, state['slots']) is None:
        state = {}
    if state and args.entry and args.entry != state['boot_id']:
        fail('The saved claim belongs to another boot entry. Finish --unbind before selecting a new entry.')
    devices = common.enumerate_pci()
    claim = claim_for(devices, args, state)
    if not claim.functions:
        fail('No GPU slot selected.')
    foreign_config_check()
    if args.bind:
        common.guard_claim(claim, devices, args.allow_boot_vga)
        common.check_id_collisions(devices, claim)
        check_groups(claim, require=False)
    active_guest_claims(claim)
    entry = common.resolve_boot_entry(state if state else ({'boot_id': args.entry} if args.entry else None))
    images = common.preflight(entry, enable=True, managed_cmdline=CMDLINE_FRAGMENT)
    softdeps, blacklist = common.drm_blacklist_for(devices, claim)
    # The Phase 3 selection can include vendors sharing a host driver. Never
    # blacklist survivors, including shared HDMI/audio/USB infrastructure.
    drivers = {*common.VENDOR_DRM.get(d.vendor, ()) for d in claim.functions if d.klass4 in common.GPU_CLASSES}
    if state:
        host_options = state['host_options']
        host_files = state['host_files']
    else:
        adopted = MODPROBE.exists()
        for path in (MODPROBE, MODULES, CMDLINE_FRAGMENT):
            if path.exists() and not re.search(r'^# Managed by (?:Arsonix|Dusky)', path.read_text(encoding='utf-8'), re.MULTILINE):
                fail(f'{path} is not a recognized managed file. Preserve/consolidate it before adopting this toggle.')
        current = parameters(common.read_target_options(entry))
        host_options = remove_claim(current, claim.ids, drivers) if adopted else current
        host_files = {str(MODPROBE): host_modprobe(MODPROBE.read_text(encoding='utf-8'), claim.ids, drivers) if adopted else None,
                      str(MODULES): None}
    desired = dict(host_options)
    files = dict(host_files)
    if args.bind:
        vendor = common.cpu_vendor()
        if vendor in ('intel', 'amd'):
            key = vendor + '_iommu'
            values = set(desired.get(key, '').strip('"').split(',')) - {'', 'off'}
            if vendor == 'intel':
                values.add('on')
            elif args.amd_force_enable:
                values.add('force_enable')
            if values:
                desired[key] = ','.join(sorted(values))
            else:
                desired.pop(key, None)
        desired['iommu'] = 'pt'
        for key, values in (('vfio-pci.ids', claim.ids), ('module_blacklist', blacklist)):
            values = set(values) | set(desired.get(key, '').strip('"').split(',')) - {''}
            if values:
                desired[key] = ','.join(sorted(values))
        inherited = host_files.get(str(MODPROBE)) or ''
        extra_options = []
        for line in inherited.splitlines():
            if re.prefixmatch(r'\s*options\s+vfio[-_]pci\b', line):
                for option in shlex.split(line, comments=True)[2:]:
                    if option.startswith('ids='):
                        desired['vfio-pci.ids'] = ','.join(sorted(set(desired['vfio-pci.ids'].split(',')) | set(option[4:].split(','))))
                    else:
                        extra_options.append(option)
        inherited = re.sub(r'^\s*options\s+vfio[-_]pci\s+.*\n?', '', inherited, flags=re.MULTILINE)
        files[str(MODPROBE)] = (MARKER + '\n' + inherited + 'options vfio-pci ids=' + desired['vfio-pci.ids']
                               + (' ' + shlex.join(extra_options) if extra_options else '') + '\n'
                               + ''.join(f'softdep {m} pre: vfio-pci\n' for m in sorted(softdeps))
                               + ''.join(f'blacklist {m}\n' for m in sorted(blacklist)))
        files[str(MODULES)] = module_payload()
        validate_modules(files[str(MODULES)])
    files.update(boot_payload(entry, desired))
    return claim, entry, images, desired, files, host_options, host_files


def apply_files(files):
    changed = False
    for name, payload in files.items():
        path = Path(name)
        if payload is None:
            if path.exists() or path.is_symlink():
                common.remove_file(path)
                changed = True
        else:
            changed = common.atomic_write(path, payload) or changed
    return changed


def files_match(files):
    for name, payload in files.items():
        path = Path(name)
        try:
            if payload is None:
                path.lstat()
                return False
            if path.read_text(encoding='utf-8') != payload:
                return False
        except FileNotFoundError:
            if payload is not None:
                return False
    return True


def verify_images(images, binding):
    for image in images:
        listing = checked(['lsinitcpio', '--nocolor', '--list', str(image)], timeout=120)
        paths = {line.removeprefix('./') for line in listing.splitlines()}
        if binding and str(MODPROBE).lstrip('/') not in paths:
            fail(f'{image} is missing the VFIO modprobe configuration.')
        if binding and not any(re.search(r'/vfio[-_]pci\.ko(?:\.|$)', p) for p in paths):
            fail(f'{image} is missing vfio_pci.')
        if not binding and not MODPROBE.exists() and str(MODPROBE).lstrip('/') in paths:
            fail(f'{image} still contains the removed VFIO configuration.')


def status(state):
    devices = common.enumerate_pci()
    common.render_gpus(devices, common.gpu_slots(devices))
    if not state:
        print('No toggle state recorded. Use --bind or --unbind to select/adopt a slot.')
        return 2
    print(f"Staged mode: {state['mode']}; incomplete operation: {state['pending']}; entry: {state['boot_id']}")
    live = {d.addr: d for d in devices}
    try:
        matches = files_match(state.get('staged_files', {}))
    except PermissionError:
        matches = None
        print('Boot configuration is unreadable as this user; run this command with sudo for the complete check. Live drivers are shown below.')
    ok = not state['pending'] and matches is True
    if matches is False:
        print('Staged configuration has drifted; rerun the requested action to repair it.')
    for record in state['functions']:
        dev = live.get(record['addr'])
        good = bool(dev and dev.ids == record['ids'] and
                    (dev.driver == 'vfio-pci' if state['mode'] == 'bind' else dev.driver not in (None, 'vfio-pci', 'pci-stub')))
        print(f"{record['addr']}: {dev.driver if dev else 'absent'}; {'applied' if good else 'not applied'}")
        ok = ok and good
    if state['mode'] == 'bind' and ok:
        check_groups(common.Claim([live[r['addr']] for r in state['functions']]), require=True)
    if matches is not None:
        print('Boot configuration and live driver assignments verified.' if ok else 'Reboot is pending or a driver/device needs investigation. Rerun the requested action if staging was interrupted.')
    return 0 if ok else 2


def main():
    parser = argparse.ArgumentParser(description=__doc__, allow_abbrev=False)
    actions = parser.add_mutually_exclusive_group(required=True)
    actions.add_argument('--bind', action='store_true', help='stage VFIO isolation for next boot')
    actions.add_argument('--unbind', action='store_true', help='stage host-driver return for next boot')
    actions.add_argument('--status', action='store_true', help='compare saved intent and actual drivers')
    parser.add_argument('--slot', help='PCI GPU slot, with optional domain/function')
    parser.add_argument('--entry', help='systemd-boot entry ID on first use')
    parser.add_argument('--allow-boot-vga', action='store_true', help='explicitly allow the host boot/last GPU')
    parser.add_argument('--amd-force-enable', action='store_true', help='force AMD-Vi only for firmware requiring it')
    parser.add_argument('--dry-run', action='store_true', help='preflight and show plan without writes/build/reboot')
    parser.add_argument('--no-rebuild', action='store_true', help='stage only; leave an incomplete journal until rebuilt')
    args = parser.parse_args()
    if args.status and any((args.slot, args.entry, args.allow_boot_vga, args.amd_force_enable, args.dry_run, args.no_rebuild)):
        parser.error('--status takes no additional options')
    common.check_versions()
    if not args.status:
        common.elevate()
    # Same lock as the GPU disabler: mutually exclusive operations across tools.
    with COMMON_PATH.open('rb') as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            fail('Another GPU toggle operation is running.')
        state = saved_state()
        if args.status:
            return status(state)
        claim, entry, images, desired, files, host_options, host_files = plan(args, state)
        mode = 'bind' if args.bind else 'unbind'
        print(f"Stage {mode}: {', '.join(claim.addrs)}; IDs {', '.join(claim.ids)}; boot entry {entry.ident}")
        for name, payload in files.items():
            print(f"{'Remove' if payload is None else 'Write'} {name}")
            if payload is not None:
                print(payload.rstrip())
        if args.dry_run:
            print('Preview complete; no writes, image rebuild or reboot.')
            return 0
        converged = bool(state and not state['pending'] and state['mode'] == mode
                         and state.get('staged_files') == files and files_match(files))
        if converged:
            try:
                verify_images(images, args.bind)
                refreshed = common.find_boot_entry(ident=entry.ident)
                converged = bool(refreshed and parameters(refreshed.options if entry.kind == 'type2' else common.read_target_options(refreshed)) == desired)
            except RuntimeError:
                converged = False
        if converged:
            print('Already convergent; no writes or rebuild. Use --status to verify the live drivers.')
            return 0
        data = {'schema': 1, 'mode': mode, 'pending': True, 'boot_id': entry.ident,
                'slots': claim.slots, 'functions': [{'addr': d.addr, 'ids': d.ids} for d in claim.functions],
                'host_options': host_options, 'host_files': host_files, 'staged_files': files}
        # Durable journal precedes the first configuration write; retry the same
        # action after a build/write interruption. Never snapshot a partial bind.
        save(data)
        apply_files(files)
        if args.no_rebuild:
            print('Configuration staged, images NOT rebuilt. Rerun without --no-rebuild before rebooting.')
            return 2
        subprocess.run(['mkinitcpio', '-P'], check=True, stdin=subprocess.DEVNULL)
        verify_images(images, args.bind)
        refreshed = common.find_boot_entry(ident=entry.ident)
        if refreshed is None or parameters(refreshed.options if entry.kind == 'type2' else common.read_target_options(refreshed)) != desired:
            fail('Staged boot options do not match the requested mode. Journal retained; do not reboot.')
        data['pending'] = False
        save(data)
        phase = load_json(PHASE_STATE)
        phase.setdefault('schema', 2)
        phase.update(vfio_ids=claim.ids, vfio_addrs=claim.addrs, vfio_nodedevs=['pci_' + a.replace(':', '_').replace('.', '_') for a in claim.addrs],
                     vfio_slot=claim.slots[0])
        common.atomic_write(PHASE_STATE, json.dumps(phase, indent=2, sort_keys=True) + '\n')
        print('Boot configuration verified. Reboot to apply; --status verifies actual drivers afterward. No reboot was initiated.')
        return 0


if __name__ == '__main__':
    try:
        raise SystemExit(main())
    except KeyboardInterrupt:
        sys.exit('Interrupted. If staging began, its journal is retained; rerun the requested action before rebooting.')
    except (OSError, ValueError, KeyError, RuntimeError, subprocess.CalledProcessError, ET.ParseError) as exc:
        sys.exit(f'VFIO toggle failed: {exc}')
