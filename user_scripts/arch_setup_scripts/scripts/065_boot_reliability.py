#!/usr/bin/env python3
#d: Apply Dusky boot and shutdown reliability settings
"""Install shared fixes; preserve existing charge and login policy. No daemon."""
import argparse
import os
from pathlib import Path
import re
import stat
import subprocess
import tempfile

USER_SCRIPTS = Path(__file__).resolve().parents[2]
ASSETS = USER_SCRIPTS / 'arch_iso_scripts/offline_iso/assets'
SYSTEMD = Path('/etc/systemd/system')
VENDOR_SYSTEMD = Path('/usr/lib/systemd/system')
RULES = Path('/etc/udev/rules.d/60-dusky-ioscheduler.rules')


def command(*args, timeout=300):
    return subprocess.run(args, text=True, capture_output=True, check=True, timeout=timeout).stdout.strip()


def atomic_write(path, text, mode=0o644):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode='w', encoding='utf-8', dir=path.parent,
                                         prefix='.dusky-boot-', delete=False) as stream:
            temporary = Path(stream.name)
            if stat.S_IMODE(os.fstat(stream.fileno()).st_mode) != mode:
                os.fchmod(stream.fileno(), mode)
            stream.write(text)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
        descriptor = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def publish(path, text, dry_run):
    previous = path.read_text(encoding='utf-8') if path.exists() else None
    if previous == text:
        return False
    print(f'{"Would update" if dry_run else "Updating"}: {path}')
    if not dry_run:
        mode = stat.S_IMODE(path.stat().st_mode) if path.exists() else 0o644
        backup = path.with_name(path.name + '.dusky-boot-backup')
        if previous is not None and not backup.exists():
            atomic_write(backup, previous, mode)
        atomic_write(path, text, mode)
    return True


def remove_fsck_skip(content):
    """Remove only the exact kernel token, preserving quoted values and init args."""
    lines = content.splitlines(keepends=True)
    options = []
    for index, line in enumerate(lines):
        match = re.match(r'[ \t]*options[ \t]+([^\r\n]*)', line, re.IGNORECASE)
        if match:
            value = match[1].strip()
            if len(value) >= 2 and value[0] in "\"'" and value[-1] == value[0]:
                value = value[1:-1]
            options.append((index, value))
    value = ' '.join(value for _, value in options)
    if 'fsck.mode=skip' not in value:
        return content
    if value.count('"') % 2:
        raise ValueError('Unbalanced kernel command-line quotes; entry left unchanged')
    parts = re.findall(r'\s+|(?:[^\s"]|"[^"]*")+', value)
    changed = False
    for index, token in enumerate(parts):
        unquoted = token[1:-1] if token.startswith('"') and token.endswith('"') else token
        if unquoted == '--':
            break
        if unquoted == 'fsck.mode=skip':
            parts[index] = ''
            changed = True
    if not changed:
        return content
    updated = ''.join(parts).strip()
    if len(updated) >= 2 and updated[0] in "\"'" and updated[-1] == updated[0]:
        quote = '"' if updated[0] == "'" else "'"
        updated = quote + updated + quote
    first = options[0][0]
    ending = '\r\n' if lines[first].endswith('\r\n') else '\n'
    lines[first] = f'options {updated}{ending}'
    for index, _ in options[1:]:
        lines[index] = ''
    return ''.join(lines)


def boot_paths():
    if not Path('/sys/firmware/efi').exists():
        return []
    check = subprocess.run(['bootctl', '--no-pager', 'is-installed'], text=True,
                           capture_output=True, timeout=15)
    if check.returncode == 1 and check.stdout.strip() == 'no':
        return []
    check.check_returncode()
    paths = [Path(command('bootctl', '--no-pager', option))
             for option in ('--print-esp-path', '--print-boot-path')]
    if not all(path.is_absolute() for path in paths):
        raise ValueError('bootctl returned a non-absolute boot path')
    return list(dict.fromkeys(paths))


def apply(dry_run=False):
    # Read and validate boot entries before changing files.
    entries = {}
    for root in boot_paths():
        for path in sorted((root / 'loader/entries').glob('*.conf')):
            content = path.read_text(encoding='utf-8')
            if re.search(r'^[ \t]*linux[ \t]+\S', content, re.MULTILINE | re.IGNORECASE):
                entries[path] = remove_fsck_skip(content)
    installed = subprocess.run(['pacman', '-Qq', 'rtkit'], capture_output=True, text=True, timeout=15)
    if installed.returncode:
        if dry_run:
            print('Would install missing rtkit from the configured repositories.')
        else:
            # Never kill a package transaction because a wall-clock timer expired.
            command('pacman', '-S', '--needed', '--noconfirm', 'rtkit', timeout=None)
    publish(RULES, (ASSETS / RULES.name).read_text(encoding='utf-8'), dry_run)
    for action in ('reboot', 'poweroff', 'halt', 'kexec'):
        service = f'plymouth-{action}.service'
        if (VENDOR_SYSTEMD / service).exists():
            publish(SYSTEMD / f'{service}.d/90-dusky-font-cache.conf',
                    (ASSETS / 'plymouth-font-cache.conf').read_text(encoding='utf-8'), dry_run)
    for path, content in entries.items():
        publish(path, content, dry_run)
    # Upgrade only an already-enabled, exactly recognized autologin override.
    # Never select autologin or change an account/greeter policy here.
    override = SYSTEMD / 'getty@tty1.service.d/override.conf'
    if override.exists():
        content = override.read_text(encoding='utf-8')
        match = re.fullmatch(r'\[Service\]\nExecStart=\nExecStart=-/usr/bin/agetty --autologin ([a-zA-Z0-9_][a-zA-Z0-9_.-]*) --noclear --noissue %I \$TERM\n?', content)
        autologin = USER_SCRIPTS / 'arch_setup_scripts/scripts/285_tty_autologin.sh'
        if match and command('bash', str(autologin), '--status') == 'true':
            if dry_run:
                print(f'Would update the existing autologin override for {match[1]}.')
            else:
                command('bash', str(autologin), '--auto', '--user', match[1])
    # Refresh an existing Dusky battery unit without choosing a new limit.
    battery = SYSTEMD / 'battery-charge-limit.service'
    if battery.exists():
        content = battery.read_text(encoding='utf-8')
        limit = re.search(r'^Description=Hardware Battery Charge Limit \(([1-9][0-9]?|100)%{1,2}\)$', content, re.MULTILINE)
        enabled = subprocess.run(['systemctl', '--root=/', 'is-enabled', 'battery-charge-limit.service'],
                                 capture_output=True, text=True, timeout=15).stdout.strip() == 'enabled'
        if content.startswith('# Managed by battery_limiter.sh\n') and limit and enabled:
            if dry_run:
                print(f'Would refresh the managed battery unit, preserving {limit[1]}%.')
            else:
                command('bash', str(USER_SCRIPTS / 'battery/limiter/battery_limiter.sh'),
                        '--limit', limit[1], '--persist')
    if not dry_run:
        command('systemctl', 'daemon-reload')
        command('udevadm', 'control', '--reload')
    print('Boot reliability settings checked. No filesystem repair or cache deletion was performed.')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--dry-run', action='store_true')
    args = parser.parse_args()
    if not args.dry_run and os.geteuid() != 0:
        parser.error('Run with sudo, or use --dry-run.')
    apply(args.dry_run)


if __name__ == '__main__':
    main()
