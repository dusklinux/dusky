"""Small real-rsync fixtures; no system mounts, dependencies or kernel builds."""
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import kernel_storage as s


def run(argv):
    subprocess.run(argv, check=True, capture_output=True)


class StorageTests(unittest.TestCase):
    def settings(self, root):
        disk = root / 'disk'
        return dict(persistent_dir=disk, packages_dir=disk / 'packages',
                    thinlto_dir=disk / 'thinlto-cache', ccache_dir=disk / 'ccache',
                    zram_dir=root / 'ram', ram_reserve_gib=8)

    def test_only_selected_identity_restored_and_other_disk_trees_preserved(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp); cfg = self.settings(root)
            selected = 'linux-7.3-rc5+battery-current'
            other = ('linux-7.3-rc4+battery-old', 'linux-7.3-rc5+battery-different')
            for name in (selected, *other):
                obj = cfg['persistent_dir'] / 'src' / name / 'kernel/test.o'
                obj.parent.mkdir(parents=True); obj.write_bytes(name.encode())
            with patch.object(s, 'ram_mount', side_effect=lambda p: root / 'ram' in p.parents):
                with s.ram_workspace(cfg, run, lambda _: None, tree_name=selected) as ram:
                    self.assertEqual([p.name for p in (ram / 'src').iterdir()], [selected])
                    obj = ram / 'src' / selected / 'kernel/test.o'
                    obj.write_bytes(b'updated selected object')
                    # Package staging is disposable and must not be checkpointed.
                    stage = ram / 'src' / selected / 'pacman/pkg/temporary'
                    stage.parent.mkdir(parents=True); stage.write_bytes(b'disposable')
            self.assertEqual((cfg['persistent_dir'] / 'src' / selected / 'kernel/test.o').read_bytes(), b'updated selected object')
            self.assertFalse((cfg['persistent_dir'] / 'src' / selected / 'pacman').exists())
            for name in other:
                self.assertEqual((cfg['persistent_dir'] / 'src' / name / 'kernel/test.o').read_bytes(), name.encode())

    def test_switching_tree_drops_saved_ram_copy_without_deleting_disk_copy(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp); cfg = self.settings(root)
            with patch.object(s, 'ram_mount', side_effect=lambda p: root / 'ram' in p.parents):
                with s.ram_workspace(cfg, run, lambda _: None, tree_name='linux-old') as ram:
                    (ram / 'src/linux-old/test.o').write_bytes(b'old build')
                    (ram / 'ccache/shared').write_bytes(b'reusable cache')
                with s.ram_workspace(cfg, run, lambda _: None, tree_name='linux-new') as ram:
                    self.assertEqual([p.name for p in (ram / 'src').iterdir()], ['linux-new'])
                    self.assertEqual((ram / 'ccache/shared').read_bytes(), b'reusable cache')
                    (ram / 'src/linux-new/test.o').write_bytes(b'new build')
                self.assertEqual((cfg['persistent_dir'] / 'src/linux-old/test.o').read_bytes(), b'old build')
                self.assertEqual((cfg['persistent_dir'] / 'src/linux-new/test.o').read_bytes(), b'new build')

    def test_unsaved_tree_is_protected_before_switching_cleanup(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp); cfg = self.settings(root)
            with patch.object(s, 'ram_mount', side_effect=lambda p: root / 'ram' in p.parents):
                with s.ram_workspace(cfg, run, lambda _: None, tree_name='linux-old') as ram:
                    pass
                (ram / 'src/linux-old/test.o').write_bytes(b'unsaved build')
                (ram / '.unsaved').touch()
                with self.assertRaisesRegex(s.StorageError, 'Unsaved'):
                    with s.ram_workspace(cfg, run, lambda _: None, tree_name='linux-new'):
                        self.fail('Must not delete uncheckpointed work')
                self.assertEqual((ram / 'src/linux-old/test.o').read_bytes(), b'unsaved build')

    def test_restore_save_and_reboot_restore(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp); cfg = self.settings(root)
            obj = cfg['persistent_dir'] / 'src/linux/kernel/test.o'
            obj.parent.mkdir(parents=True); obj.write_bytes(b'previous compiled object')
            timestamp = obj.stat().st_mtime_ns
            with patch.object(s, 'ram_mount', side_effect=lambda p: root / 'ram' in p.parents):
                with s.ram_workspace(cfg, run, lambda _: None, tree_name='linux') as ram:
                    restored = ram / 'src/linux/kernel/test.o'
                    self.assertEqual(restored.stat().st_mtime_ns, timestamp)
                    self.assertEqual(restored.read_bytes(), obj.read_bytes())
                    (ram / 'ccache/result').write_bytes(b'cached compile')
                    restored.write_bytes(b'new object')
                self.assertEqual(obj.read_bytes(), b'new object')
                self.assertEqual((cfg['ccache_dir'] / 'result').read_bytes(), b'cached compile')
                import shutil
                shutil.rmtree(root / 'ram')
                with s.ram_workspace(cfg, run, lambda _: None, tree_name='linux') as ram:
                    self.assertEqual((ram / 'src/linux/kernel/test.o').read_bytes(), b'new object')
                    self.assertEqual((ram / 'ccache/result').read_bytes(), b'cached compile')

    def test_failure_still_checkpoints(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp); cfg = self.settings(root)
            with patch.object(s, 'ram_mount', side_effect=lambda p: root / 'ram' in p.parents):
                with self.assertRaises(KeyboardInterrupt):
                    with s.ram_workspace(cfg, run, lambda _: None, tree_name='linux') as ram:
                        (ram / 'src/linux/partial.o').write_bytes(b'reusable')
                        raise KeyboardInterrupt()
            self.assertEqual((cfg['persistent_dir'] / 'src/linux/partial.o').read_bytes(), b'reusable')

    def test_failed_save_protects_unsaved_work(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp); cfg = self.settings(root)
            with patch.object(s, 'ram_mount', side_effect=lambda p: root / 'ram' in p.parents):
                with self.assertRaises(OSError):
                    def broken_run(argv):
                        if str(root / 'ram') in argv[-2]:
                            raise OSError('disk full')
                        run(argv)
                    with s.ram_workspace(cfg, broken_run, lambda _: None, tree_name='linux'):
                        pass
                with self.assertRaisesRegex(s.StorageError, 'Unsaved'):
                    with s.ram_workspace(cfg, run, lambda _: None, tree_name='linux'):
                        self.fail('Must not overwrite unsaved RAM data')

    def test_missing_mount_rejected(self):
        with tempfile.TemporaryDirectory() as tmp, patch.object(s, 'ram_mount', return_value=False):
            with self.assertRaisesRegex(s.StorageError, 'not on a mounted'):
                with s.ram_workspace(self.settings(Path(tmp)), run, lambda _: None, tree_name='linux'):
                    self.fail()

    def test_volatile_package_destination_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp); cfg = self.settings(root); cfg['packages_dir'] = root / 'ram/packages'
            with patch.object(s, 'ram_mount', side_effect=lambda p: root / 'ram' in p.parents):
                with self.assertRaisesRegex(s.StorageError, 'Package destination'):
                    with s.ram_workspace(cfg, run, lambda _: None, tree_name='linux'):
                        self.fail()

    def test_build_dir_override_updates_default_cache_paths(self):
        template = Path(__file__).resolve().parents[1] / 'kernel_profiles' / 'settings' / 'kernel_settings.toml'
        with patch.dict('os.environ', {}, clear=True):
            cfg = s.load_settings(template, Path('/old/cache'), Path('/new/build'))
        self.assertEqual(cfg['packages_dir'], Path('/new/build/packages'))
        self.assertEqual(cfg['thinlto_dir'], Path('/new/build/thinlto-cache'))

    def test_settings_validation_and_defaults(self):
        template = Path(__file__).resolve().parents[1] / 'kernel_profiles' / 'settings' / 'kernel_settings.toml'
        with patch.dict('os.environ', {}, clear=True):
            cfg = s.load_settings(template, Path('/disk/cache'))
            self.assertEqual(cfg['packages_dir'], Path('/disk/cache/dusky-kernel/packages'))
            self.assertEqual(cfg['persistent_dir'], Path('/disk/cache/dusky-kernel'))
        with tempfile.TemporaryDirectory() as tmp:
            bad = Path(tmp) / 'bad.toml'
            bad.write_text(template.read_text().replace('ram_reserve_gib = 8', 'ram_reserve_gib = true'))
            with self.assertRaises(s.StorageError):
                s.load_settings(bad, Path(tmp))


class CapacityTests(unittest.TestCase):
    def test_missing_mount_selects_disk(self):
        with patch.object(s, 'ram_mount', return_value=False):
            ready, _ = s.ram_capacity({'zram_dir': Path('/ram')}, 'thin', 'linux')
            self.assertFalse(ready)

    def test_capacity_accounts_for_caches_memory_and_reusable_workspace(self):
        from types import SimpleNamespace
        cfg = dict(zram_dir=Path('/tmp'), persistent_dir=Path('/disk'),
                   thinlto_dir=Path('/cache/lto'), ccache_dir=Path('/cache/ccache'), ram_reserve_gib=8)
        gib = 1 << 30
        for free, memory, reusable, expected in ((40, 40, 0, True), (20, 40, 0, False),
                                                  (40, 20, 0, False), (10, 18, 20, True)):
            with self.subTest(free=free, memory=memory, reusable=reusable):
                def size(path):
                    if path in (cfg['thinlto_dir'], cfg['ccache_dir']): return 2 * gib
                    if path.parent == cfg['zram_dir']: return reusable * gib
                    return 0
                with patch.object(s, 'ram_mount', return_value=True), patch.object(s, 'tree_bytes', side_effect=size), \
                     patch.object(s.shutil, 'disk_usage', return_value=SimpleNamespace(free=free*gib)), \
                     patch.object(Path, 'read_text', return_value=f'MemAvailable: {memory * 1048576} kB\n'):
                    self.assertEqual(s.ram_capacity(cfg, 'thin', 'linux')[0], expected)

    def test_ram_capacity_identifies_physical_ram_sufficiency(self):
        from types import SimpleNamespace
        cfg = dict(zram_dir=Path('/tmp'), persistent_dir=Path('/disk'),
                   thinlto_dir=Path('/cache/lto'), ccache_dir=Path('/cache/ccache'), ram_reserve_gib=8)
        gib = 1 << 30
        meminfo_64g = f"MemTotal: {64 * 1048576} kB\nMemAvailable: {10 * 1048576} kB\n"
        meminfo_16g = f"MemTotal: {16 * 1048576} kB\nMemAvailable: {10 * 1048576} kB\n"
        with patch.object(s, 'ram_mount', return_value=True), patch.object(s, 'tree_bytes', return_value=0), \
             patch.object(s.shutil, 'disk_usage', return_value=SimpleNamespace(free=10 * gib)):
            with patch.object(Path, 'read_text', return_value=meminfo_64g):
                res_64 = s.ram_capacity(cfg, 'thin', 'linux')
                self.assertFalse(res_64.ready)
                self.assertTrue(res_64.phys_ram_sufficient)
                self.assertEqual(res_64.total_ram, 64 * gib)
            with patch.object(Path, 'read_text', return_value=meminfo_16g):
                res_16 = s.ram_capacity(cfg, 'thin', 'linux')
                self.assertFalse(res_16.ready)
                self.assertFalse(res_16.phys_ram_sufficient)
                self.assertEqual(res_16.total_ram, 16 * gib)
