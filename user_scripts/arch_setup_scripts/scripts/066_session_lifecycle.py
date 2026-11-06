#!/usr/bin/env python3
#d: Migrate Dusky to on-demand session shutdown without an idle listener
"""Keep Hyprland's vendor target and retire the old Dusky shutdown listener."""
import argparse
import hashlib
from pathlib import Path
import subprocess

LEGACY_TARGET_HASH = '3b34e1030d535b4994aaf8a01e7c7e4b7008d3c1fed8c5625878c5a58f2ac461'
VENDOR_TARGET = Path('/usr/lib/systemd/user/hyprland-session.target')


def command(*args):
    return subprocess.run(['systemctl', '--user', *args], check=True,
                          text=True, capture_output=True, timeout=15).stdout.strip()


def retire(path, dry_run):
    print(f'{"Would retire" if dry_run else "Retiring"}: {path}')
    if dry_run:
        return
    backup = path.with_name(path.name + '.dusky-retired')
    if backup.exists():
        if backup.read_bytes() != path.read_bytes():
            raise FileExistsError(f'Different backup already exists: {backup}')
        path.unlink()
    else:
        path.rename(backup)


def apply(home=None, dry_run=False):
    home = Path.home() if home is None else home
    if not VENDOR_TARGET.is_file():
        raise RuntimeError('Install the supported Hyprland package providing its session target first.')
    directory = home / '.config/systemd/user'
    target = directory / 'hyprland-session.target'
    if target.is_file():
        if hashlib.sha256(target.read_bytes()).hexdigest() == LEGACY_TARGET_HASH:
            retire(target, dry_run)
        else:
            print(f'Custom target retained: {target}; verify its graphical-session stop propagation.')
    service = directory / 'dusky-session-shutdown.service'
    if service.is_file():
        if 'ExecStart=/usr/bin/python3 %h/user_scripts/hypr/session_shutdown.py' in service.read_text(encoding='utf-8'):
            retire(service, dry_run)
        else:
            print(f'Custom service retained: {service}')
    execution = command('show', 'dusky-session-shutdown.service', '-p', 'ExecStart', '--value')
    if '/user_scripts/hypr/session_shutdown.py' in execution:
        if dry_run:
            print('Would stop the old Dusky shutdown listener.')
        else:
            command('stop', 'dusky-session-shutdown.service')
    if not dry_run:
        command('daemon-reload')
    print('Rofi/wlogout use the on-demand action helper; no shutdown listener is installed.')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--dry-run', action='store_true')
    apply(dry_run=parser.parse_args().dry_run)


if __name__ == '__main__':
    main()
