"""Check offline Plymouth configuration without changing the host or launching it."""
import configparser
import importlib.util
from pathlib import Path
import tempfile
import unittest
from unittest import mock

SCRIPT = Path(__file__).resolve().parents[1] / '135_plymouth_setup.py'


def load_installer():
    spec = importlib.util.spec_from_file_location('plymouth_installer_fixture', SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class PlymouthShutdown(unittest.TestCase):
    def test_import_never_runs_package_commands(self):
        with mock.patch('importlib.util.find_spec', return_value=None), mock.patch(
                'subprocess.run', side_effect=AssertionError('Import must not run commands')):
            load_installer()

    def test_pipeline_preserves_custom_configuration_and_is_repeatable(self):
        module = load_installer()
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            custom = root / 'plymouth-reboot.service.d/50-custom.conf'
            custom.parent.mkdir()
            custom.write_text('[Service]\nNice=5\n', encoding='utf-8')
            with mock.patch.object(module, 'SYSTEMD_UNIT_DIR', root), \
                    mock.patch.object(module, 'ensure_root'), \
                    mock.patch.object(module, 'ensure_plymouth'), \
                    mock.patch.object(module, 'deploy_theme'), \
                    mock.patch.object(module, 'patch_mkinitcpio'), \
                    mock.patch.object(module.console, 'print'), \
                    mock.patch('subprocess.run', side_effect=AssertionError('No live commands')):
                module.main()
                before = {p.relative_to(root): p.read_bytes() for p in root.rglob('*.conf')}
                module.main()
            self.assertEqual(before, {p.relative_to(root): p.read_bytes() for p in root.rglob('*.conf')})
            self.assertEqual(custom.read_text(encoding='utf-8'), '[Service]\nNice=5\n')
            expected = {f'plymouth-{action}.service.d' for action in ('reboot', 'poweroff', 'halt', 'kexec')}
            self.assertEqual({p.name for p in root.iterdir()}, expected)
            for service in expected:
                dropin = root / service / '90-dusky-font-cache.conf'
                config = configparser.ConfigParser(interpolation=None)
                config.read(dropin, encoding='utf-8')
                self.assertEqual(dict(config['Service']), {'environment': 'FONTCONFIG_USE_MMAP=0'})
                self.assertEqual(dropin.stat().st_mode & 0o777, 0o644)

    def test_write_failure_aborts_before_theme_and_initramfs_changes(self):
        module = load_installer()
        with tempfile.TemporaryDirectory() as directory:
            blocked = Path(directory) / 'not-a-directory'
            blocked.write_text('fixture', encoding='utf-8')
            with mock.patch.object(module, 'SYSTEMD_UNIT_DIR', blocked), \
                    mock.patch.object(module, 'ensure_root'), \
                    mock.patch.object(module, 'ensure_plymouth'), \
                    mock.patch.object(module, 'deploy_theme') as theme, \
                    mock.patch.object(module, 'patch_mkinitcpio') as initramfs, \
                    mock.patch.object(module.console, 'print'):
                with self.assertRaises(OSError):
                    module.main()
                theme.assert_not_called()
                initramfs.assert_not_called()


if __name__ == '__main__':
    unittest.main()
