"""Exercise package-lock waits without opening the real pacman database."""
import asyncio
import importlib.util
import os
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

SCRIPT = Path(__file__).resolve().parents[1] / 'scripts/060_package_installation.py'


class PackageLocking(unittest.IsolatedAsyncioTestCase):
    @classmethod
    def setUpClass(cls):
        spec = importlib.util.spec_from_file_location('package_lock_fixture', SCRIPT)
        cls.module = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = cls.module
        spec.loader.exec_module(cls.module)

    def app(self):
        return SimpleNamespace(ctx=SimpleNamespace(is_root=True),
                               log_system=Mock(), status_label=Mock(),
                               _is_package_manager_active=Mock(return_value=False))

    async def test_absent_lock_needs_no_wait(self):
        with tempfile.TemporaryDirectory() as directory:
            lock = Path(directory) / 'db.lck'
            with patch.object(self.module, 'PACMAN_DB_LOCK', lock):
                self.assertTrue(await self.module.EliteInstallerApp.resolve_pacman_lock(self.app()))
            self.assertFalse(lock.exists())

    async def test_timeout_preserves_lock_even_when_owner_name_is_unrecognized(self):
        for held in (False, True):
            with self.subTest(open_owner=held), tempfile.TemporaryDirectory() as directory:
                lock = Path(directory) / 'db.lck'
                lock.write_text('fixture transaction')
                inode = lock.stat().st_ino
                descriptor = os.open(lock, os.O_RDONLY) if held else None
                timeout = asyncio.timeout
                try:
                    with patch.object(self.module, 'PACMAN_DB_LOCK', lock), \
                         patch.object(self.module.asyncio, 'timeout', side_effect=lambda _: timeout(0)):
                        result = await self.module.EliteInstallerApp.resolve_pacman_lock(self.app())
                    self.assertTrue(lock.exists(), 'Installer removed a lock it did not acquire')
                    self.assertEqual(lock.stat().st_ino, inode)
                    self.assertEqual(lock.read_text(), 'fixture transaction')
                    self.assertFalse(result)
                finally:
                    if descriptor is not None:
                        os.close(descriptor)

    async def test_continues_after_owner_releases_lock(self):
        with tempfile.TemporaryDirectory() as directory:
            lock = Path(directory) / 'db.lck'
            lock.write_text('fixture transaction')
            async def owner_release(_):
                self.assertEqual(lock.read_text(), 'fixture transaction')
                lock.unlink()
            with patch.object(self.module, 'PACMAN_DB_LOCK', lock), \
                 patch.object(self.module.asyncio, 'sleep', side_effect=owner_release) as wait:
                self.assertTrue(await self.module.EliteInstallerApp.resolve_pacman_lock(self.app()))
            wait.assert_awaited_once()
            self.assertFalse(lock.exists())


if __name__ == '__main__':
    unittest.main()
