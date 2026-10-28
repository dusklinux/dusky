"""Temporary-root coverage for the installation fixes, with no host mutations."""
import hashlib
import importlib.util
from pathlib import Path
import subprocess
import tempfile
import tomllib
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]


def load(filename):
    spec = importlib.util.spec_from_file_location('fixture_' + filename[:3], ROOT / 'scripts' / filename)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class BootSetup(unittest.TestCase):
    def test_profile_wiring_and_rtkit_manifest(self):
        for name in ('01_main', '02_iso', '03_dusk_personal'):
            profile = tomllib.loads((ROOT / 'profiles' / f'{name}.toml').read_text(encoding='utf-8'))
            scripts = profile['sequence']['scripts']
            self.assertEqual(scripts.count('S | 065_boot_reliability.py'), 1)
            self.assertEqual(scripts.count('U | 066_session_lifecycle.py'), 1)
            self.assertLess(scripts.index('S | 065_boot_reliability.py'), scripts.index('U | 066_session_lifecycle.py'))
        packages = (ROOT / 'scripts/package_profiles/01_all').read_text(encoding='utf-8').splitlines()
        self.assertEqual(packages.count('rtkit'), 1)

    def test_fsck_token_removal_preserves_quotes_force_custom_entries_and_init_args(self):
        module = load('065_boot_reliability.py')
        for content, expected in (
            ('options root=UUID=test fsck.mode=skip quiet\n', 'options root=UUID=test  quiet\n'),
            ('options root=x fsck.mode=skip\noptions fsck.mode=force -- fsck.mode=skip\n',
             'options root=x  fsck.mode=force -- fsck.mode=skip\n'),
            ('options root=x label="keep fsck.mode=skip inside"\n', None),
            ('options root=x fsck.mode=skip \"fsck.mode=skip\" quiet\n', 'options root=x   quiet\n'),
            ('options root=x -- fsck.mode=skip\n', None),
        ):
            self.assertEqual(module.remove_fsck_skip(content), content if expected is None else expected)
        with self.assertRaises(ValueError):
            module.remove_fsck_skip('options root="unclosed fsck.mode=skip\n')

    def test_setup_repeatability_backups_and_existing_battery_limit(self):
        module = load('065_boot_reliability.py')
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            systemd = root / 'etc/systemd/system'
            systemd.mkdir(parents=True)
            vendor = root / 'usr/lib/systemd/system'
            vendor.mkdir(parents=True)
            for action in ('reboot', 'poweroff', 'halt', 'kexec'):
                (vendor / f'plymouth-{action}.service').touch()
            (systemd / 'battery-charge-limit.service').write_text(
                '# Managed by battery_limiter.sh\nDescription=Hardware Battery Charge Limit (73%%)\n', encoding='utf-8')
            entry = root / 'boot/loader/entries/arch-linux-fallback.conf'
            entry.parent.mkdir(parents=True)
            original = 'title Recovery\nlinux /vmlinuz-linux\noptions root=UUID=fixture fsck.mode=skip\n'
            entry.write_text(original, encoding='utf-8')
            custom = entry.with_name('windows.conf')
            custom.write_text('efi /EFI/Microsoft/Boot/bootmgfw.efi\noptions fsck.mode=skip\n', encoding='utf-8')
            calls = []
            with patch.object(module, 'SYSTEMD', systemd), patch.object(module, 'VENDOR_SYSTEMD', vendor), \
                    patch.object(module, 'RULES', root / 'etc/udev/rules.d/60-dusky-ioscheduler.rules'), \
                    patch.object(module, 'boot_paths', return_value=[root / 'boot']), \
                    patch.object(module.subprocess, 'run', side_effect=lambda args, **kw: subprocess.CompletedProcess(args, 0, 'enabled\n', '')), \
                    patch.object(module, 'command', side_effect=lambda *args: calls.append(args) or ''):
                module.apply()
                first = {p.relative_to(root): p.read_bytes() for p in root.rglob('*') if p.is_file()}
                module.apply()
                self.assertEqual(first, {p.relative_to(root): p.read_bytes() for p in root.rglob('*') if p.is_file()})
            self.assertNotIn('fsck.mode=skip', entry.read_text(encoding='utf-8'))
            self.assertEqual(entry.with_name(entry.name + '.dusky-boot-backup').read_text(encoding='utf-8'), original)
            self.assertIn('fsck.mode=skip', custom.read_text(encoding='utf-8'))
            battery_calls = [call for call in calls if '--limit' in call]
            self.assertTrue(battery_calls)
            self.assertTrue(all(call[-3:] == ('--limit', '73', '--persist') for call in battery_calls))

    def test_dry_run_never_writes_or_launches_mutating_commands(self):
        module = load('065_boot_reliability.py')
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            with patch.object(module, 'SYSTEMD', root), patch.object(module, 'RULES', root / '60-dusky-ioscheduler.rules'), \
                    patch.object(module, 'boot_paths', return_value=[]), \
                    patch.object(module.subprocess, 'run', return_value=subprocess.CompletedProcess([], 1, '', '')), \
                    patch.object(module, 'command') as command:
                module.apply(dry_run=True)
                command.assert_not_called()
            self.assertEqual(list(root.iterdir()), [])

    def test_failed_publication_preserves_previous_content_and_cleans_temporary(self):
        module = load('065_boot_reliability.py')
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            destination = root / 'entry.conf'
            destination.write_text('old', encoding='utf-8')
            with patch.object(module.os, 'replace', side_effect=OSError('fixture write failure')):
                with self.assertRaises(OSError):
                    module.atomic_write(destination, 'new')
            self.assertEqual(destination.read_text(encoding='utf-8'), 'old')
            self.assertEqual(list(root.iterdir()), [destination])

    def test_disabled_battery_persistence_is_not_enabled(self):
        module = load('065_boot_reliability.py')
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            unit = root / 'battery-charge-limit.service'
            unit.write_text('# Managed by battery_limiter.sh\nDescription=Hardware Battery Charge Limit (67%%)\n', encoding='utf-8')
            with patch.object(module, 'SYSTEMD', root), patch.object(module, 'VENDOR_SYSTEMD', root / 'vendor'), \
                    patch.object(module, 'RULES', root / '60-dusky-ioscheduler.rules'), \
                    patch.object(module, 'boot_paths', return_value=[]), \
                    patch.object(module.subprocess, 'run', return_value=subprocess.CompletedProcess([], 0, 'disabled\n', '')), \
                    patch.object(module, 'command') as command:
                module.apply()
                self.assertFalse(any('--persist' in call.args for call in command.call_args_list))

    def test_missing_rtkit_install_has_no_transaction_kill_timeout(self):
        module = load('065_boot_reliability.py')
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            with patch.object(module, 'SYSTEMD', root), patch.object(module, 'VENDOR_SYSTEMD', root / 'vendor'), \
                    patch.object(module, 'RULES', root / '60-dusky-ioscheduler.rules'), \
                    patch.object(module, 'boot_paths', return_value=[]), \
                    patch.object(module.subprocess, 'run', return_value=subprocess.CompletedProcess([], 1, '', '')), \
                    patch.object(module, 'command') as command:
                module.apply()
                command.assert_any_call('pacman', '-S', '--needed', '--noconfirm', 'rtkit', timeout=None)

    def test_only_recognized_active_autologin_is_refreshed_with_its_existing_account(self):
        module = load('065_boot_reliability.py')
        for custom in (False, True):
            with tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                override = root / 'getty@tty1.service.d/override.conf'
                override.parent.mkdir()
                content = '[Service]\nExecStart=\nExecStart=-/usr/bin/agetty --autologin portable-user --noclear --noissue %I $TERM\n'
                override.write_text(content + ('Environment=CUSTOM=yes\n' if custom else ''), encoding='utf-8')
                with patch.object(module, 'SYSTEMD', root), patch.object(module, 'VENDOR_SYSTEMD', root / 'vendor'), \
                        patch.object(module, 'RULES', root / '60-dusky-ioscheduler.rules'), \
                        patch.object(module, 'boot_paths', return_value=[]), \
                        patch.object(module.subprocess, 'run', return_value=subprocess.CompletedProcess([], 0, '', '')), \
                        patch.object(module, 'command', return_value='true') as command:
                    module.apply()
                    requests = [call.args for call in command.call_args_list if '--auto' in call.args]
                    self.assertEqual(bool(requests), not custom)
                    if requests:
                        self.assertEqual(requests[0][-3:], ('--auto', '--user', 'portable-user'))

    def test_user_migration_retires_known_target_but_preserves_custom_target(self):
        module = load('066_session_lifecycle.py')
        for custom in (False, True):
            with tempfile.TemporaryDirectory() as directory:
                home = Path(directory)
                target = home / '.config/systemd/user/hyprland-session.target'
                target.parent.mkdir(parents=True)
                target.write_text('fixture target', encoding='utf-8')
                fingerprint = hashlib.sha256(target.read_bytes()).hexdigest()
                with patch.object(module, 'VENDOR_TARGET', target), \
                        patch.object(module, 'LEGACY_TARGET_HASH', 'different' if custom else fingerprint), \
                        patch.object(module, 'command', return_value=''):
                    module.apply(home)
                self.assertEqual(target.exists(), custom)
                self.assertEqual(target.with_name(target.name + '.dusky-retired').exists(), not custom)


if __name__ == '__main__':
    unittest.main()
