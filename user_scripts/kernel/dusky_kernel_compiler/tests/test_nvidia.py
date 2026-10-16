"""NVIDIA regressions: synthetic PCI topology, real ELF imports, DKMS call ordering."""
import contextlib
import gzip
import hashlib
import io
import lzma
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch
from compression import zstd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import dusky_kernal_compile as k
import kernel_nvidia as n


class NvidiaTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.silence = contextlib.redirect_stdout(io.StringIO())
        self.silence.__enter__()
        self.addCleanup(self.silence.__exit__, None, None, None)
        callbacks = patch.object(n, 'PM_CALLBACKS_SHA256', hashlib.sha256(b'test callbacks\n').hexdigest())
        callbacks.start(); self.addCleanup(callbacks.stop)

    def source(self):
        source = self.root / 'source'
        nv_pci = source / 'kernel-open/nvidia/nv-pci.c'
        nv_pci.parent.mkdir(parents=True)
        nv_pci.write_text('test source\n')
        (nv_pci.parent / 'nv.c').write_text('test callbacks\n')
        (source / 'dkms.conf').write_text('BUILT_MODULE_LOCATION[0]="kernel-open"\n')
        return source, hashlib.sha256(nv_pci.read_bytes()).hexdigest()

    def test_repair_preserves_local_overrides_and_is_idempotent(self):
        source, digest = self.source()
        etc = self.root / 'etc'
        etc.mkdir()
        local = etc / f'nvidia-{n.VERSION}.conf'
        local.write_text('MAKE[0]="my existing command"\nPATCH[0]="local.patch"\n')
        with patch.object(n, 'ORIGINAL_SHA256', digest):
            files = n.repair_files(source, k.SCRIPT_DIR / 'compat', etc)
            for path, (text, mode) in files.items():
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(text)
                self.assertEqual(mode, '0644')
            self.assertIn('MAKE[0]="my existing command"', local.read_text())
            self.assertIn('PATCH[0]="local.patch"', local.read_text())
            self.assertEqual(n.repair_files(source, k.SCRIPT_DIR / 'compat', etc), {})

    def test_changed_source_is_not_patched(self):
        source, _ = self.source()
        with self.assertRaisesRegex(ValueError, 'differs from the tested open driver'):
            n.repair_files(source, k.SCRIPT_DIR / 'compat', self.root / 'etc')
        self.assertFalse((self.root / 'etc').exists())

    def test_changed_pm_callbacks_are_not_silently_accepted(self):
        source, digest = self.source()
        (source / 'kernel-open/nvidia/nv.c').write_text('different PM callbacks\n')
        with patch.object(n, 'ORIGINAL_SHA256', digest), self.assertRaisesRegex(ValueError, 'runtime PM callbacks differ'):
            n.repair_files(source, k.SCRIPT_DIR / 'compat', self.root / 'etc')

    def test_already_repaired_source_needs_no_overlay(self):
        source, digest = self.source()
        with patch.object(n, 'REPAIRED_SHA256', digest):
            self.assertEqual(n.repair_files(source, self.root / 'missing-assets', self.root / 'etc'), {})

    def test_repaired_source_cannot_reapply_stale_overlay(self):
        source, digest = self.source()
        etc = self.root / 'etc'; etc.mkdir()
        config = etc / f'nvidia-{n.VERSION}.conf'
        config.write_text(f'PATCH[0]="{n.PATCH_NAME}"\n')
        with patch.object(n, 'REPAIRED_SHA256', digest), self.assertRaisesRegex(ValueError, 'older RTD3 overlay'):
            n.repair_files(source, k.SCRIPT_DIR / 'compat', etc)

    def test_proprietary_source_is_not_patched(self):
        source, digest = self.source()
        (source / 'dkms.conf').write_text('BUILT_MODULE_LOCATION[0]="kernel"\n')
        with patch.object(n, 'ORIGINAL_SHA256', digest), self.assertRaises(ValueError):
            n.repair_files(source, k.SCRIPT_DIR / 'compat', self.root / 'etc')

    def test_override_only_uses_target_api_and_never_duplicates_patch(self):
        config = k.SCRIPT_DIR / 'compat' / f'nvidia-{n.VERSION}.conf'
        header = self.root / 'include/linux/fs_struct.h'
        header.parent.mkdir(parents=True)
        for present in (False, True):
            with self.subTest(api=present):
                header.write_text('#define scoped_with_init_fs() scoped_class(x)\n' if present else 'old header\n')
                script = 'PATCH=(local.patch); source "$1"; source "$1"; printf "%s\\n" "${PATCH[@]}"'
                cp = subprocess.run(['bash', '-c', script, 'test', str(config)],
                                    env={'PATH': '/usr/bin', 'kernel_source_dir': str(self.root)},
                                    check=True, capture_output=True, text=True)
                self.assertEqual(cp.stdout.splitlines(), ['local.patch'] + ([n.PATCH_NAME] if present else []))
                self.assertEqual(n.target_needs_init_fs(self.root), present)

    @unittest.skipUnless(shutil.which('cc') and (shutil.which('nm') or shutil.which('llvm-nm')), 'C toolchain required')
    def test_real_elf_import_check_in_all_module_compressions(self):
        for imported in (False, True):
            code = 'extern void *userspace_init_fs; void *test(void) { return userspace_init_fs; }' if imported else 'int test(void) { return 1; }'
            obj = self.root / 'test.ko'
            subprocess.run(['cc', '-x', 'c', '-c', '-o', str(obj), '-'], input=code, text=True, check=True)
            raw = obj.read_bytes()
            for suffix, encode in [('', bytes), ('.gz', gzip.compress), ('.xz', lzma.compress), ('.zst', zstd.compress)]:
                with self.subTest(imported=imported, suffix=suffix):
                    module = self.root / ('module.ko' + suffix)
                    module.write_bytes(encode(raw))
                    self.assertEqual(n.module_uses_init_fs(module), imported)

    def test_bad_module_does_not_silently_pass(self):
        module = self.root / 'bad.ko'
        module.write_bytes(b'not an ELF module')
        with self.assertRaises(subprocess.CalledProcessError):
            n.module_uses_init_fs(module)

    def test_corrupt_compressed_module_is_reported(self):
        for suffix in ('.xz', '.zst'):
            with self.subTest(suffix=suffix):
                module = self.root / ('bad.ko' + suffix)
                module.write_bytes(b'not a compressed module')
                with self.assertRaisesRegex(ValueError, 'Cannot decode NVIDIA module'):
                    n.module_uses_init_fs(module)

    def test_non_nvidia_path_does_not_even_import_helper(self):
        code = '''import sys
from unittest.mock import patch
import dusky_kernal_compile as k
assert "kernel_nvidia" not in sys.modules
with patch.object(k, "_gpu_vendors", return_value=("intel",)):
    assert k.prepare_nvidia_compat() is False
assert "kernel_nvidia" not in sys.modules
'''
        subprocess.run([sys.executable, '-c', code], cwd=k.SCRIPT_DIR, check=True)

    def test_no_nvidia_hardware_never_touches_dkms_or_packages(self):
        for vendors in [(), ('intel',), ('amd',), ('intel', 'amd'), ('virtio',)]:
            with self.subTest(vendors=vendors), patch.object(k, '_gpu_vendors', return_value=vendors), patch.object(k, 'run') as run, patch.object(k.PRIV, 'write_files') as write:
                self.assertFalse(k.prepare_nvidia_compat())
                run.assert_not_called()
                write.assert_not_called()

    def test_other_driver_version_never_receives_known_patch(self):
        for version in ['616.0-1', '1:616.0-2']:
            cp = subprocess.CompletedProcess([], 0, f'nvidia-open-dkms {version}\n')
            with patch.object(k, '_gpu_vendors', return_value=('amd', 'nvidia')), patch.object(k, 'run', return_value=cp), patch.object(n, 'repair_files') as repair:
                self.assertFalse(k.prepare_nvidia_compat())
                repair.assert_not_called()

    def test_nouveau_or_proprietary_package_is_not_modified(self):
        with patch.object(k, '_gpu_vendors', return_value=('nvidia',)), patch.object(k, 'run', return_value=subprocess.CompletedProcess([], 1, 'not installed')), patch.object(n, 'repair_files') as repair:
            self.assertFalse(k.prepare_nvidia_compat())
            repair.assert_not_called()

    def test_repair_rebuilds_only_target_kernel_and_checks_result(self):
        answers = [subprocess.CompletedProcess([], 0, n.VERSION), subprocess.CompletedProcess([], 0, '/tmp/nvidia.ko')] * 2
        with patch.object(n, 'target_needs_init_fs', return_value=True), patch.object(n, 'module_uses_init_fs', side_effect=[False, True]), patch.object(k, 'run', side_effect=answers), patch.object(k.PRIV, 'run') as privileged:
            k.verify_nvidia_compat('7.3-target')
            self.assertEqual([c.args[0] for c in privileged.call_args_list],
                             [['dkms', op, f'nvidia/{n.VERSION}', '-k', '7.3-target', '--force'] for op in ('build', 'install')])

    def test_already_fixed_module_does_not_rebuild(self):
        answers = [subprocess.CompletedProcess([], 0, n.VERSION), subprocess.CompletedProcess([], 0, '/tmp/nvidia.ko')]
        with patch.object(n, 'target_needs_init_fs', return_value=True), patch.object(n, 'module_uses_init_fs', return_value=True), patch.object(k, 'run', side_effect=answers), patch.object(k.PRIV, 'run') as privileged:
            k.verify_nvidia_compat('7.3-target')
            privileged.assert_not_called()

    def test_unsuccessful_repair_stops_boot_promotion(self):
        answers = [subprocess.CompletedProcess([], 0, n.VERSION), subprocess.CompletedProcess([], 0, '/tmp/nvidia.ko')] * 2
        with patch.object(n, 'target_needs_init_fs', return_value=True), patch.object(n, 'module_uses_init_fs', return_value=False), patch.object(k, 'run', side_effect=answers), patch.object(k.PRIV, 'run'), self.assertRaisesRegex(k.BuildError, 'boot promotion stopped'):
            k.verify_nvidia_compat('7.3-target')

    def test_install_prepares_before_pacman_and_verifies_before_initramfs(self):
        p = k.load_profile(k.SCRIPT_DIR / 'kernel_profiles/battery.toml')
        events = []
        def privileged(cmd, **kwargs):
            events.append(cmd[0])
            return subprocess.CompletedProcess(cmd, 0, '')
        for hardware in (False, True):
            events.clear()
            with self.subTest(nvidia=hardware), patch.object(k, 'ensure_install_dependencies'), patch.object(k.PRIV, 'ensure'), patch.object(k.PRIV, 'write_files'), patch.object(k.PRIV, 'run', side_effect=privileged), patch.object(k, 'prepare_nvidia_compat', side_effect=lambda: events.append('prepare') or hardware), patch.object(k, 'verify_nvidia_compat', side_effect=lambda _: events.append('verify')), patch.object(k, 'audit_dkms', side_effect=lambda *_: events.append('audit') or True), patch.object(k, 'prune_stale_modules'), patch.object(k, 'have', return_value=False):
                k.install_packages([], p, '7.3-target')
            self.assertLess(events.index('prepare'), events.index('pacman'))
            if hardware:
                self.assertLess(events.index('audit'), events.index('verify'))
                if 'mkinitcpio' in events:
                    self.assertLess(events.index('verify'), events.index('mkinitcpio'))
            else:
                self.assertNotIn('verify', events)

    def test_passive_power_status_uses_pci_not_drm_card_numbers(self):
        pci = self.root / 'pci'; proc = self.root / 'proc'
        for address, vendor, cls in [('0004:0a:00.0', '0x10de', '0x030200'), ('0004:0a:00.1', '0x10de', '0x040300'), ('0000:00:02.0', '0x8086', '0x030000')]:
            d = pci / address; d.mkdir(parents=True)
            (d / 'vendor').write_text(vendor); (d / 'class').write_text(cls)
        gpu = pci / '0004:0a:00.0'; (gpu / 'power').mkdir()
        (gpu / 'power_state').write_text('D3cold\n')
        (gpu / 'power/runtime_status').write_text('suspended\n')
        rows = n.power_status(pci, proc)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]['pci'], '0004:0a:00.0')
        self.assertEqual(rows[0]['power_state'], 'D3cold')
        self.assertEqual(rows[0]['driver'], 'unbound')
        self.assertEqual(rows[0]['nvidia_power'], 'unavailable')


if __name__ == '__main__':
    unittest.main()
