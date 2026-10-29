"""Tests for detecting an existing Python without touching the installation."""
import importlib.util
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

path = Path(__file__).resolve().parents[1] / "temp_python_315_installer.py"
spec = importlib.util.spec_from_file_location("dusky_python_installer", path)
installer = importlib.util.module_from_spec(spec)
spec.loader.exec_module(installer)


class ExistingRuntimeTests(unittest.TestCase):
    def check_version(self, version, accepted):
        args = SimpleNamespace(reinstall=False, force=False, no_default=False, tarball=None)
        calls = []
        def probe(_path, code):
            calls.append(code)
            if 'version_info[:3]' in code:
                return version
            if 'version_info[:2]' in code:
                return '3 14 final'
            return 'ok'
        with (patch.object(installer.platform, 'freedesktop_os_release', return_value={'ID': 'arch'}),
              patch.object(installer.shutil, 'which', return_value='/usr/bin/python3'),
              patch.object(installer, 'run_python', side_effect=probe),
              patch.object(installer, 'read_marker', return_value=None),
              patch.object(installer.tempfile, 'TemporaryDirectory', side_effect=RuntimeError('continue installation'))):
            if accepted:
                self.assertEqual(installer.cmd_install(args), 0)
                self.assertIn(installer.DESKTOP_PROBE, calls)
                self.assertIn(installer.UPDATER_PROBE, calls)
            else:
                # The older selected interpreter must not be reported as ready.
                # Stop before any archive download or filesystem changes.
                with self.assertRaisesRegex(RuntimeError, 'continue installation'):
                    installer.cmd_install(args)
                self.assertNotIn(installer.DESKTOP_PROBE, calls)

    def test_rc3_and_newer_are_preserved(self):
        for version in ('3 15 0 candidate 3', '3 15 0 final 0', '3 15 1 final 0', '3 16 0 final 0'):
            with self.subTest(version=version):
                self.check_version(version, True)

    def test_older_interpreters_do_not_satisfy_floor(self):
        for version in ('3 14 8 final 0', '3 15 0 alpha 7', '3 15 0 candidate 2'):
            with self.subTest(version=version):
                self.check_version(version, False)

    def test_selected_runtime_missing_dependencies_does_not_install_over_it(self):
        args = SimpleNamespace(reinstall=False, force=False, no_default=False, tarball=None)
        with (patch.object(installer.platform, 'freedesktop_os_release', return_value={'ID': 'arch'}),
              patch.object(installer.shutil, 'which', return_value='/usr/bin/python3'),
              patch.object(installer, 'run_python', side_effect=['3 15 1 final 0', RuntimeError('missing')]),
              patch.object(installer, 'read_marker', return_value=None) as marker):
            with self.assertRaisesRegex(RuntimeError, 'missing'):
                installer.cmd_install(args)
            marker.assert_called_once()

    def test_older_managed_runtime_is_upgraded(self):
        args = SimpleNamespace(reinstall=False, force=False, no_default=False, tarball=None)
        marker = {'version': '3.15.0rc3', 'arch': installer.ARCH_TAG, 'files': []}
        with (patch.object(installer.platform, 'freedesktop_os_release', return_value={'ID': 'arch'}),
              patch.object(installer, 'read_marker', return_value=marker),
              patch.object(installer, 'run_python', return_value='3 14 final'),
              patch.object(installer.tempfile, 'TemporaryDirectory', side_effect=RuntimeError('upgrade'))):
            with self.assertRaisesRegex(RuntimeError, 'upgrade'):
                installer.cmd_install(args)

    def test_newer_managed_runtime_is_not_downgraded(self):
        args = SimpleNamespace(reinstall=False, force=False, no_default=False, tarball=None)
        marker = {'version': '3.15.99', 'arch': installer.ARCH_TAG, 'files': []}
        with (patch.object(installer.platform, 'freedesktop_os_release', return_value={'ID': 'arch'}),
              patch.object(installer, 'read_marker', return_value=marker)):
            with self.assertRaisesRegex(RuntimeError, 'newer'):
                installer.cmd_install(args)


if __name__ == '__main__':
    unittest.main()
