#!/usr/bin/env python3
"""One-shot Bluetooth recovery shared by Dusky Tray and the proximity TUI."""
import fcntl
import json
import os
import subprocess
import sys
import time
from pathlib import Path

UNIT = 'dusky_proximity.service'
BUSCTL = '/usr/bin/busctl'
SYSTEMCTL = '/usr/bin/systemctl'
SUDO = '/usr/bin/sudo'


class RecoveryError(RuntimeError):
    pass


def run(*args: str, timeout: float = 20, check: bool = True) -> subprocess.CompletedProcess:
    result = subprocess.run(args, capture_output=True, text=True, encoding='utf-8',
                            stdin=subprocess.DEVNULL, timeout=timeout)
    if check and result.returncode:
        detail = result.stderr.strip() or result.stdout.strip() or f'exit {result.returncode}'
        raise RecoveryError(f'{" ".join(args)}: {detail}')
    return result


def adapters() -> list[str]:
    """Wait for BlueZ's objects to return after a service restart."""
    deadline = time.monotonic() + 10
    while True:
        result = run(BUSCTL, '--system', '--timeout=2', '--json=short', 'call',
                     'org.bluez', '/', 'org.freedesktop.DBus.ObjectManager',
                     'GetManagedObjects', timeout=3, check=False)
        if result.returncode == 0:
            objects = json.loads(result.stdout)['data'][0]
            paths = sorted(path for path, interfaces in objects.items()
                           if 'org.bluez.Adapter1' in interfaces)
            if paths:
                return paths
        if time.monotonic() >= deadline:
            raise RecoveryError('No Bluetooth adapter became available after restarting BlueZ.')
        time.sleep(0.2)


def power_on(path: str) -> None:
    """BlueZ may still be restoring adapter power when its objects first appear."""
    deadline = time.monotonic() + 10
    detail = ''
    while True:
        state = run(BUSCTL, '--system', '--timeout=2', 'get-property', 'org.bluez',
                    path, 'org.bluez.Adapter1', 'Powered', timeout=3, check=False)
        if state.returncode == 0 and state.stdout.strip() == 'b true':
            return
        result = run(BUSCTL, '--system', '--timeout=2', 'set-property', 'org.bluez', path,
                     'org.bluez.Adapter1', 'Powered', 'b', 'true', timeout=3, check=False)
        detail = result.stderr.strip() or state.stderr.strip() or 'adapter remains powered off'
        if time.monotonic() >= deadline:
            raise RecoveryError(f'Could not power on {path}: {detail}')
        time.sleep(0.2)


def recover() -> str:
    state = run(SYSTEMCTL, '--user', 'is-active', UNIT, check=False).stdout.strip()
    resume = state in {'active', 'activating', 'reloading'}
    failure = None
    try:
        if resume:
            run(SYSTEMCTL, '--user', 'stop', UNIT)
        run(SUDO, '-n', '/usr/bin/rfkill', 'unblock', 'bluetooth', timeout=5)
        run(SUDO, '-n', SYSTEMCTL, 'restart', 'bluetooth.service')
        paths = adapters()
        for path in paths:
            power_on(path)
    except (OSError, ValueError, KeyError, IndexError, subprocess.SubprocessError, RecoveryError) as exc:
        failure = str(exc)
    finally:
        if resume:
            try:
                run(SYSTEMCTL, '--user', 'start', UNIT)
            except (OSError, subprocess.SubprocessError, RecoveryError) as exc:
                message = f'Could not restore proximity monitoring: {exc}'
                failure = f'{failure}; {message}' if failure else message
    if failure:
        raise RecoveryError(failure)
    return 'Bluetooth restarted and enabled. Paired devices can reconnect.'


def notify(success: bool) -> None:
    try:
        run('/usr/bin/notify-send', '-a', 'Dusky Proximity', '-i',
            'bluetooth-active' if success else 'bluetooth-disabled',
            'Bluetooth recovered' if success else 'Recovery failed',
            'Bluetooth is enabled' if success else 'Check Bluetooth service', timeout=3, check=False)
    except (OSError, subprocess.SubprocessError):
        pass  # Desktop notification availability must not change recovery's result.


def main() -> int:
    runtime = Path(os.environ.get('XDG_RUNTIME_DIR') or f'/run/user/{os.getuid()}')
    try:
        # The descriptor holds the lock across stop/restart/restore and notification.
        with (runtime / 'dusky-bluetooth-recovery.lock').open('a', encoding='utf-8') as lock:
            try:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                print('Bluetooth recovery is already running.')
                return 0
            try:
                message = recover()
            except RecoveryError as exc:
                print(f'Bluetooth recovery failed: {exc}', file=sys.stderr)
                notify(False)
                return 1
            print(message)
            notify(True)
            return 0
    except OSError as exc:
        print(f'Bluetooth recovery could not start: {exc}', file=sys.stderr)
        notify(False)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
