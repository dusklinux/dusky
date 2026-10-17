"""Recovery fixtures never change the live Bluetooth service."""
import contextlib
import importlib.util
import io
import json
import os
from pathlib import Path
import subprocess
import tempfile
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import patch

spec = importlib.util.spec_from_file_location('bluetooth_recovery', Path(__file__).resolve().parents[1] / 'bluetooth_recovery.py')
recovery = importlib.util.module_from_spec(spec)
spec.loader.exec_module(recovery)


class RecoveryTests(unittest.TestCase):
    def fixture(self, state='active', failure=None):
        calls = []
        powered = set()

        def run(*args, **kwargs):
            calls.append(args)
            if failure and failure in args:
                raise recovery.RecoveryError('fixture failure: ' + failure)
            if 'is-active' in args:
                return subprocess.CompletedProcess(args, 0 if state == 'active' else 3, state + '\n', '')
            if 'GetManagedObjects' in args:
                return subprocess.CompletedProcess(args, 0, json.dumps({'data': [{
                    '/org/bluez/hci7/dev_AA_BB': {'org.bluez.Device1': {}},
                    '/org/bluez/hci7': {'org.bluez.Adapter1': {}},
                    '/org/bluez/hci2': {'org.bluez.Adapter1': {}},
                }]}), '')
            if 'set-property' in args:
                powered.add(args[5])
            value = ('b true\n' if args[5] in powered else 'b false\n') if 'get-property' in args else ''
            return subprocess.CompletedProcess(args, 0, value, '')

        return calls, run

    def test_recovers_all_adapters_and_restores_active_observer_in_order(self):
        calls, run = self.fixture()
        with patch.object(recovery, 'run', side_effect=run):
            message = recovery.recover()
        self.assertIn('restarted and enabled', message)
        stop = (recovery.SYSTEMCTL, '--user', 'stop', recovery.UNIT)
        reset = (recovery.SUDO, '-n', recovery.SYSTEMCTL, 'restart', 'bluetooth.service')
        start = (recovery.SYSTEMCTL, '--user', 'start', recovery.UNIT)
        self.assertLess(calls.index(stop), calls.index(reset))
        self.assertEqual(calls[-1], start)
        powered = [c[5] for c in calls if 'set-property' in c]
        self.assertEqual(powered, ['/org/bluez/hci2', '/org/bluez/hci7'])
        self.assertFalse(any('Connect' in c or 'StartDiscovery' in c for c in calls))

    def test_inactive_or_missing_observer_stays_inactive(self):
        for state in ['inactive', 'unknown', 'failed']:
            with self.subTest(state=state):
                calls, run = self.fixture(state)
                with patch.object(recovery, 'run', side_effect=run):
                    recovery.recover()
                self.assertFalse(any('--user' in c and ('start' in c or 'stop' in c) for c in calls))

    def test_failures_restore_observer_and_report_error(self):
        for failure in ['stop', 'unblock', 'restart', 'set-property', 'get-property']:
            with self.subTest(failure=failure):
                calls, run = self.fixture(failure=failure)
                with patch.object(recovery, 'run', side_effect=run), self.assertRaisesRegex(recovery.RecoveryError, 'fixture failure'):
                    recovery.recover()
                self.assertEqual(calls[-1], (recovery.SYSTEMCTL, '--user', 'start', recovery.UNIT))

    def test_restore_failure_is_not_reported_as_success(self):
        _, run = self.fixture(failure='start')
        with patch.object(recovery, 'run', side_effect=run), self.assertRaisesRegex(recovery.RecoveryError, 'Could not restore'):
            recovery.recover()

    def test_no_adapter_is_a_bounded_failure(self):
        empty = subprocess.CompletedProcess([], 0, '{"data": [{}]}', '')
        with patch.object(recovery, 'run', return_value=empty), patch.object(recovery.time, 'monotonic', side_effect=[0, 11]), self.assertRaisesRegex(recovery.RecoveryError, 'No Bluetooth adapter'):
            recovery.adapters()

    def test_bluez_startup_is_retried(self):
        unavailable = subprocess.CompletedProcess([], 1, '', 'BlueZ not ready')
        available = subprocess.CompletedProcess([], 0, '{"data": [{"/org/bluez/hci9": {"org.bluez.Adapter1": {}}}]}', '')
        with patch.object(recovery, 'run', side_effect=[unavailable, available]), patch.object(recovery.time, 'sleep'):
            self.assertEqual(recovery.adapters(), ['/org/bluez/hci9'])

    def test_adapter_power_startup_race_uses_verified_state(self):
        off = subprocess.CompletedProcess([], 0, 'b false\n', '')
        pending = subprocess.CompletedProcess([], 1, '', 'power change in progress')
        on = subprocess.CompletedProcess([], 0, 'b true\n', '')
        with patch.object(recovery, 'run', side_effect=[off, pending, on]), patch.object(recovery.time, 'sleep'):
            recovery.power_on('/org/bluez/hci9')

    def test_adapter_power_failure_is_bounded(self):
        off = subprocess.CompletedProcess([], 0, 'b false\n', '')
        blocked = subprocess.CompletedProcess([], 1, '', 'Blocked through rfkill')
        with patch.object(recovery, 'run', side_effect=[off, blocked]), patch.object(recovery.time, 'monotonic', side_effect=[0, 11]), self.assertRaisesRegex(recovery.RecoveryError, 'Blocked through rfkill'):
            recovery.power_on('/org/bluez/hci9')

    def test_timeout_still_restores_observer(self):
        calls, base = self.fixture()

        def run(*args, **kwargs):
            if 'restart' in args:
                raise subprocess.TimeoutExpired(args, 20)
            return base(*args, **kwargs)

        with patch.object(recovery, 'run', side_effect=run), self.assertRaises(recovery.RecoveryError):
            recovery.recover()
        self.assertEqual(calls[-1], (recovery.SYSTEMCTL, '--user', 'start', recovery.UNIT))

    def test_concurrent_clicks_execute_only_one_recovery_and_one_notification(self):
        entered = threading.Event()
        release = threading.Event()

        def slow():
            entered.set()
            if not release.wait(30):
                raise recovery.RecoveryError('fixture release timed out')
            return 'Recovered.'

        with tempfile.TemporaryDirectory() as directory, patch.dict(os.environ, {'XDG_RUNTIME_DIR': directory}), patch.object(recovery, 'recover', side_effect=slow) as recover, patch.object(recovery, 'notify') as notify, contextlib.redirect_stdout(io.StringIO()), ThreadPoolExecutor(max_workers=16) as pool:
            first = pool.submit(recovery.main)
            try:
                self.assertTrue(entered.wait(10))
                duplicates = [pool.submit(recovery.main) for _ in range(128)]
                self.assertTrue(all(f.result(timeout=10) == 0 for f in duplicates))
                self.assertEqual(recover.call_count, 1)
            finally:
                release.set()
            self.assertEqual(first.result(timeout=10), 0)
            notify.assert_called_once_with(True)

    def test_notification_failure_does_not_change_recovery_success(self):
        with patch.object(recovery, 'run', side_effect=FileNotFoundError('notify-send missing')):
            recovery.notify(True)


if __name__ == '__main__':
    unittest.main()
