"""Resolve distribution QEMU defaults without assuming an account name/UID."""
import importlib.util
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

path = Path(__file__).resolve().parents[1] / '07_storage_setup.py'
spec = importlib.util.spec_from_file_location('storage_identity_tests', path)
storage = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = storage
spec.loader.exec_module(storage)


class StorageIdentityTests(unittest.TestCase):
    def resolve(self, config, caps, user=('fixture', 321), group=('fixture-group', 654)):
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp) / 'qemu.conf'; p.write_text(config)
            with (patch.object(storage, 'QEMU_CONF', p),
                  patch.object(storage, 'run', return_value=storage.Cmd([], 0, caps, '')) as run,
                  patch.object(storage, '_lookup_user', return_value=user),
                  patch.object(storage, '_lookup_group', return_value=group)):
                result = storage.resolve_qemu_identity()
                return result, run.call_count

    def test_commented_user_is_not_root_default(self):
        caps = "<capabilities><host><secmodel><model>dac</model><baselabel type='kvm'>+321:+654</baselabel></secmodel></host></capabilities>"
        result, calls = self.resolve('#user="root"\n#group="root"\n', caps)
        self.assertEqual((result.uid, result.gid), (321, 654))
        self.assertEqual(calls, 1)

    def test_explicit_pair_needs_no_daemon_query(self):
        result, calls = self.resolve('user="fixture"\ngroup="fixture-group"\n', '')
        self.assertEqual((result.user, result.group), ('fixture', 'fixture-group'))
        self.assertEqual(calls, 0)

    def test_missing_dac_does_not_silently_assume_root(self):
        with self.assertRaises(SystemExit):
            self.resolve('', '<capabilities><host/></capabilities>')

    def test_windows_profile_initializes_daemon_before_storage(self):
        import tomllib
        data = tomllib.loads((path.parent / 'profile/02_windows_11.toml').read_text())
        names = [t['script'] for t in data['sequence']['tasks']]
        self.assertLess(names.index('10_virt_modular_daemon.py'), names.index('07_storage_setup.py'))


if __name__ == '__main__':
    unittest.main()
