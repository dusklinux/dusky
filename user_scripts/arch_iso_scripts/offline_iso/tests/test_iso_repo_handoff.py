"""Factory/switcher handoff fixtures; no host database writes or network access."""
import hashlib
import importlib.util
import io
import os
from pathlib import Path
import subprocess
import sys
import tarfile
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


class IsoRepositoryHandoff(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.factory = load('factory_handoff_fixture', ROOT / 'iso_maker/python/dusky_iso_generator.py')
        cls.switcher = load('switcher_handoff_fixture', ROOT / '051_pacman_repo_switch.py')

    def test_factory_timeout_preserves_lock_with_or_without_an_open_owner(self):
        module = self.factory
        for held in (False, True):
            with tempfile.TemporaryDirectory() as directory:
                lock = Path(directory) / 'db.lck'
                lock.write_text('fixture owner', encoding='utf-8')
                descriptor = os.open(lock, os.O_RDONLY) if held else None
                before = lock.stat().st_ino
                try:
                    with patch.object(module, 'HOST_DB_LOCK', lock):
                        with self.assertRaises(module.FactoryError):
                            module.wait_for_host_pacman_lock(lambda _: None, timeout=0)
                    self.assertEqual(lock.stat().st_ino, before)
                    self.assertEqual(lock.read_text(encoding='utf-8'), 'fixture owner')
                finally:
                    if descriptor is not None:
                        os.close(descriptor)

    def test_factory_wait_continues_only_after_owner_releases_lock(self):
        module = self.factory
        with tempfile.TemporaryDirectory() as directory:
            lock = Path(directory) / 'db.lck'
            lock.touch()
            def release(_):
                self.assertTrue(lock.exists())
                lock.unlink()  # simulated owner release, not factory removal
            with patch.object(module, 'HOST_DB_LOCK', lock), patch.object(module.time, 'sleep', side_effect=release) as sleep:
                module.wait_for_host_pacman_lock(lambda _: None)
                sleep.assert_called_once()

    def test_partial_repository_does_not_hide_later_complete_candidate(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            partial, ready = root / 'partial', root / 'ready'
            partial.mkdir()
            ready.mkdir()
            (partial / 'fixture-1-1-any.pkg.tar.zst').touch()
            (ready / 'archrepo.db.tar.zst').touch()
            (ready / 'archrepo.db').symlink_to('archrepo.db.tar.zst')
            with patch.object(self.switcher, 'OFFLINE_REPO_CANDIDATES', (partial, ready)):
                self.assertEqual(self.switcher.get_offline_repo_path(), f'file://{ready}')

    def test_payload_staging_preserves_switcher_and_shared_assets(self):
        module = self.factory
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / 'source'
            source.mkdir()
            (source / '000_dusky_arch_install.sh').write_text('#!/bin/bash\n', encoding='utf-8')
            switcher = (ROOT / '051_pacman_repo_switch.py').read_bytes()
            (source / '051_pacman_repo_switch.py').write_bytes(switcher)
            (source / 'assets').mkdir()
            for name in ('60-dusky-ioscheduler.rules', 'plymouth-font-cache.conf'):
                (source / 'assets' / name).write_bytes((ROOT / 'assets' / name).read_bytes())
            config = module.IsoConfig(root / 'workspace', source, root / 'selected-checkout', root / 'official', None, root / 'output')
            config.profile_dir.mkdir(parents=True)
            (config.profile_dir / 'packages.x86_64').write_text('base\n', encoding='utf-8')
            module.stage_payloads(config)
            staged = config.profile_dir / 'airootfs/root/arch_install'
            self.assertEqual((staged / '051_pacman_repo_switch.py').read_bytes(), switcher)
            for name in ('60-dusky-ioscheduler.rules', 'plymouth-font-cache.conf'):
                self.assertEqual((staged / 'assets' / name).read_bytes(), (source / 'assets' / name).read_bytes())

    def test_rtkit_is_targeted_by_generator_and_installer(self):
        self.assertIn('rtkit', self.factory.ALL_GROUPS['audio'])
        targets = self.factory.installer_package_targets(ROOT)
        self.assertEqual(targets.count('rtkit'), 1)

    def test_real_repository_index_is_accepted_by_switcher_and_pacman(self):
        module = self.factory
        with tempfile.TemporaryDirectory() as directory:
            repo = Path(directory) / 'repo'
            repo.mkdir()
            package = repo / 'dusky-handoff-fixture-1.0-1-any.pkg.tar.zst'
            metadata = b'pkgname = dusky-handoff-fixture\npkgbase = dusky-handoff-fixture\npkgver = 1.0-1\npkgdesc = offline handoff fixture\nbuilddate = 1\nsize = 5\narch = any\nlicense = MIT\n'
            with tarfile.open(package, 'w:zst') as archive:
                info = tarfile.TarInfo('.PKGINFO')
                info.size = len(metadata)
                archive.addfile(info, io.BytesIO(metadata))
            entries = module.repo_add_entries([package])
            module.write_repo_db(repo, entries.values())
            self.assertTrue(module.repo_db_links_ok(repo))
            self.assertEqual(module.read_repo_db(repo / module.DB_NAME)['dusky-handoff-fixture'].sha256,
                             hashlib.sha256(package.read_bytes()).hexdigest())
            with patch.object(self.switcher, 'OFFLINE_REPO_CANDIDATES', (repo,)):
                self.assertEqual(self.switcher.get_offline_repo_path(), f'file://{repo}')
                config, mirror = Path(directory) / 'pacman.conf', Path(directory) / 'mirrorlist'
                with patch.object(self.switcher, 'wait_for_pacman_lock'), patch.object(
                        self.switcher.subprocess, 'run', return_value=subprocess.CompletedProcess([], 0, 'fixture sync')):
                    self.switcher.switch_to_offline(config, mirror)
                text = config.read_text(encoding='utf-8').replace('Include = /etc/pacman.d/mirrorlist', f'Include = {mirror}')
                config.write_text(text, encoding='utf-8')
                result = subprocess.run(['pacman-conf', '--config', str(config), '--repo', 'archrepo', 'Server'],
                                        check=True, capture_output=True, text=True)
                self.assertEqual(result.stdout.strip(), f'file://{repo}')
            module.verify_repo_closure(repo, ['dusky-handoff-fixture'])

    def test_selected_checkout_and_installer_override_remain_distinct(self):
        module = self.factory
        class StopAtIso(Exception):
            pass
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            checkout, override = root / 'selected-checkout', root / 'installer-override'
            checkout.mkdir()
            override.mkdir()
            args = module.parse_args(['--action', 'iso', '--auto', '--ref', 'fixture-selected-ref',
                                      '--source-dir', str(override), '--workspace', str(root / 'work'),
                                      '--output-dir', str(root / 'output')])
            captured = []
            def capture(config, user):
                captured.append(config)
                raise StopAtIso()
            user = module.RealUser('fixture', 1234, 1234, root, (1234,))
            with patch.object(module.os, 'geteuid', return_value=0), patch.object(module.signal, 'signal'), \
                    patch.object(module.atexit, 'register'), patch.object(module, 'acquire_factory_lock'), \
                    patch.object(module, 'real_user', return_value=user), \
                    patch.object(module, 'checkout_build_source', return_value=checkout) as select, \
                    patch.object(module, 'iso_phase', side_effect=capture):
                with self.assertRaises(StopAtIso):
                    module.main(args)
            select.assert_called_once_with('fixture-selected-ref')
            self.assertEqual(captured[0].checkout, checkout)
            self.assertEqual(captured[0].source_dir, override)


if __name__ == '__main__':
    unittest.main()
