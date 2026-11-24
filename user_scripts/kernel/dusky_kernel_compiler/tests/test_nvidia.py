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
        p = patch.object(n, 'PM_CALLBACKS_SHA256', hashlib.sha256(b'test callbacks\n').hexdigest())
        p.start()
        self.addCleanup(p.stop)

    def source(self):
        source = self.root / 'source'
        nv_pci = source / 'kernel-open/nvidia/nv-pci.c'
        nv_pci.parent.mkdir(parents=True)
        nv_pci.write_text('test source\n', encoding='utf-8')
        (nv_pci.parent / 'nv.c').write_text('test callbacks\n', encoding='utf-8')
        (source / 'dkms.conf').write_text('BUILT_MODULE_LOCATION[0]="kernel-open"\n', encoding='utf-8')
        return source, hashlib.sha256(nv_pci.read_bytes()).hexdigest()

    def write(self, files):
        for path, (text, mode) in files.items():
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(text, encoding='utf-8')
            self.assertEqual(mode, '0644')

    def test_validated_version_and_fresh_config_parity(self):
        self.assertEqual(n.VERSION, '620.73.04')
        source, digest = self.source()
        etc = self.root / 'etc'
        compat = k.SCRIPT_DIR / 'compat'
        with patch.object(n, 'ORIGINAL_SHA256', digest):
            files = n.repair_files(source, compat, etc)
            self.write(files)
            config_path = etc / f'nvidia-{n.VERSION}.conf'
            patch_path = etc / 'nvidia/patches' / n.RTD3_PATCH_NAME
            self.assertEqual(set(files), {config_path, patch_path})
            self.assertEqual(config_path.read_text(encoding='utf-8'),
                             (compat / f'nvidia-{n.VERSION}.conf').read_text(encoding='utf-8'))
            self.assertEqual(patch_path.read_text(encoding='utf-8'),
                             (compat / n.RTD3_PATCH_NAME).read_text(encoding='utf-8'))
            self.assertEqual(n.repair_files(source, compat, etc), {})

    def test_repair_preserves_local_overrides_and_is_idempotent(self):
        source, digest = self.source()
        compat = k.SCRIPT_DIR / 'compat'
        for suffix in ('\n', ''):
            with self.subTest(trailing_newline=bool(suffix)):
                etc = self.root / f'etc-{len(suffix)}'
                etc.mkdir()
                local = etc / f'nvidia-{n.VERSION}.conf'
                local.write_text('MAKE[0]="my existing command"\nPATCH[0]="local.patch"' + suffix, encoding='utf-8')
                with patch.object(n, 'ORIGINAL_SHA256', digest):
                    files = n.repair_files(source, compat, etc)
                    self.write(files)
                    text = local.read_text(encoding='utf-8')
                    self.assertIn('MAKE[0]="my existing command"\n', text)
                    self.assertIn('PATCH[0]="local.patch"\n', text)
                    self.assertIn(n.RTD3_PATCH_NAME, text)
                    self.assertTrue(text.endswith('\n'))
                    self.assertEqual(n.repair_files(source, compat, etc), {})

    def test_missing_source_files_are_rejected(self):
        for rel in ('kernel-open/nvidia/nv-pci.c', 'kernel-open/nvidia/nv.c', 'dkms.conf'):
            with self.subTest(missing=rel):
                source = self.root / ('missing-' + rel.replace('/', '_'))
                nv_pci = source / 'kernel-open/nvidia/nv-pci.c'
                nv_pci.parent.mkdir(parents=True)
                nv_pci.write_text('test source\n', encoding='utf-8')
                (nv_pci.parent / 'nv.c').write_text('test callbacks\n', encoding='utf-8')
                (source / 'dkms.conf').write_text('BUILT_MODULE_LOCATION[0]="kernel-open"\n', encoding='utf-8')
                (source / rel).unlink()
                with self.assertRaisesRegex(ValueError, 'missing'):
                    n.repair_files(source, k.SCRIPT_DIR / 'compat', self.root / 'etc')

    def test_changed_source_is_not_patched(self):
        source, _ = self.source()
        with self.assertRaisesRegex(ValueError, 'differs from the tested open driver'):
            n.repair_files(source, k.SCRIPT_DIR / 'compat', self.root / 'etc')
        self.assertFalse((self.root / 'etc').exists())

    def test_changed_pm_callbacks_are_not_silently_accepted(self):
        source, digest = self.source()
        (source / 'kernel-open/nvidia/nv.c').write_text('different PM callbacks\n', encoding='utf-8')
        with patch.object(n, 'ORIGINAL_SHA256', digest), self.assertRaisesRegex(ValueError, 'runtime PM callbacks differ'):
            n.repair_files(source, k.SCRIPT_DIR / 'compat', self.root / 'etc')

    def test_already_repaired_source_needs_no_overlay(self):
        source, digest = self.source()
        with patch.object(n, 'REPAIRED_SHA256', digest):
            self.assertEqual(n.repair_files(source, self.root / 'missing-assets', self.root / 'etc'), {})

    def test_repaired_source_cannot_reapply_stale_overlay(self):
        source, digest = self.source()
        etc = self.root / 'etc'
        etc.mkdir()
        config = etc / f'nvidia-{n.VERSION}.conf'
        config.write_text(f'PATCH[0]="{n.RTD3_PATCH_NAME}"\n', encoding='utf-8')
        with patch.object(n, 'REPAIRED_SHA256', digest), self.assertRaisesRegex(ValueError, 'older overlay'):
            n.repair_files(source, k.SCRIPT_DIR / 'compat', etc)

    def test_proprietary_source_is_not_patched(self):
        source, digest = self.source()
        (source / 'dkms.conf').write_text('BUILT_MODULE_LOCATION[0]="kernel"\n', encoding='utf-8')
        with patch.object(n, 'ORIGINAL_SHA256', digest), self.assertRaises(ValueError):
            n.repair_files(source, k.SCRIPT_DIR / 'compat', self.root / 'etc')

    def test_override_only_uses_target_api_and_never_duplicates_patch(self):
        config = k.SCRIPT_DIR / 'compat' / f'nvidia-{n.VERSION}.conf'
        fs_header = self.root / 'include/linux/fs_struct.h'
        fs_header.parent.mkdir(parents=True)
        cases = (
            ('old_header', 'old header\n', False),
            ('comment_only', '/* Callers should use scoped_with_init_fs() */\n', False),
            ('macro_defined', '#define scoped_with_init_fs() scoped_class(x)\n', True),
        )
        for label, header_text, fs_present in cases:
            with self.subTest(case=label):
                fs_header.write_text(header_text, encoding='utf-8')
                script = 'PATCH=(local.patch); source "$1"; source "$1"; printf "%s\\n" "${PATCH[@]}"'
                cp = subprocess.run(['bash', '-c', script, 'test', str(config)],
                                    env={'PATH': '/usr/bin', 'kernel_source_dir': str(self.root)},
                                    check=True, capture_output=True, text=True)
                expected = ['local.patch', n.RTD3_PATCH_NAME] if fs_present else ['local.patch']
                self.assertEqual(cp.stdout.splitlines(), expected)
                self.assertEqual(n.target_needs_init_fs(self.root), fs_present)

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

    def test_prepare_covers_intel_amd_and_nvidia_only_topologies(self):
        expected_source = Path('/var/lib/dkms/nvidia') / n.VERSION / 'source'
        real_is_dir = Path.is_dir

        def fake_is_dir(path):
            return True if path == expected_source else real_is_dir(path)

        cp = subprocess.CompletedProcess([], 0, f'nvidia-open-dkms {n.VERSION}-1\n')
        planned = {Path('/etc/dkms') / f'nvidia-{n.VERSION}.conf': ('content\n', '0644')}
        for vendors in (('intel', 'nvidia'), ('amd', 'nvidia'), ('nvidia',)):
            with self.subTest(vendors=vendors), patch.object(k, '_gpu_vendors', return_value=vendors), \
                 patch.object(k, 'run', return_value=cp), patch.object(Path, 'is_dir', fake_is_dir), \
                 patch.object(n, 'repair_files', return_value=planned) as repair, \
                 patch.object(k.PRIV, 'write_files') as write:
                self.assertTrue(k.prepare_nvidia_compat())
                repair.assert_called_once_with(expected_source, k.SCRIPT_DIR / 'compat', Path('/etc/dkms'))
                write.assert_called_once_with(planned)

    def test_other_driver_version_never_receives_known_patch(self):
        for version in ('615.78.08-1', '620.70.06-1', '621.0-1', '1:621.0-2'):
            cp = subprocess.CompletedProcess([], 0, f'nvidia-open-dkms {version}\n')
            with self.subTest(version=version), patch.object(k, '_gpu_vendors', return_value=('intel', 'nvidia')), \
                 patch.object(k, 'run', return_value=cp), patch.object(n, 'repair_files') as repair:
                self.assertFalse(k.prepare_nvidia_compat())
                repair.assert_not_called()

    def test_nouveau_or_proprietary_package_is_not_modified(self):
        with patch.object(k, '_gpu_vendors', return_value=('nvidia',)), patch.object(k, 'run', return_value=subprocess.CompletedProcess([], 1, 'not installed')), patch.object(n, 'repair_files') as repair:
            self.assertFalse(k.prepare_nvidia_compat())
            repair.assert_not_called()

    def test_target_without_init_fs_skips_verify_rebuild(self):
        with patch.object(n, 'target_needs_init_fs', return_value=False), patch.object(k.PRIV, 'run') as privileged:
            self.assertFalse(k.verify_nvidia_compat('7.2-target'))
            privileged.assert_not_called()

    def test_repair_rebuilds_only_target_kernel_and_checks_result(self):
        answers = [subprocess.CompletedProcess([], 0, n.VERSION), subprocess.CompletedProcess([], 0, '/tmp/nvidia.ko')] * 2
        with patch.object(n, 'target_needs_init_fs', return_value=True), patch.object(n, 'module_uses_init_fs', side_effect=[False, True]), patch.object(k, 'run', side_effect=answers), patch.object(k.PRIV, 'run') as privileged:
            self.assertTrue(k.verify_nvidia_compat('7.4-target'))
            self.assertEqual([c.args[0] for c in privileged.call_args_list],
                             [['dkms', op, f'nvidia/{n.VERSION}', '-k', '7.4-target', '--force'] for op in ('build', 'install')])

    def test_already_fixed_module_does_not_rebuild(self):
        answers = [subprocess.CompletedProcess([], 0, n.VERSION), subprocess.CompletedProcess([], 0, '/tmp/nvidia.ko')]
        with patch.object(n, 'target_needs_init_fs', return_value=True), patch.object(n, 'module_uses_init_fs', return_value=True), patch.object(k, 'run', side_effect=answers), patch.object(k.PRIV, 'run') as privileged:
            self.assertFalse(k.verify_nvidia_compat('7.4-target'))
            privileged.assert_not_called()

    def test_unsuccessful_repair_stops_boot_promotion(self):
        answers = [subprocess.CompletedProcess([], 0, n.VERSION), subprocess.CompletedProcess([], 0, '/tmp/nvidia.ko')] * 2
        with patch.object(n, 'target_needs_init_fs', return_value=True), patch.object(n, 'module_uses_init_fs', return_value=False), patch.object(k, 'run', side_effect=answers), patch.object(k.PRIV, 'run'), self.assertRaisesRegex(k.BuildError, 'boot promotion stopped'):
            k.verify_nvidia_compat('7.4-target')

    def test_install_prepares_before_pacman_and_verifies_before_initramfs(self):
        p = k.load_profile(k.SCRIPT_DIR / 'kernel_profiles/balanced.toml')
        events = []

        def privileged(cmd, **kwargs):
            events.append(cmd[0])
            return subprocess.CompletedProcess(cmd, 0, '')

        for hardware in (False, True):
            events.clear()
            with self.subTest(nvidia=hardware), patch.object(k, 'ensure_install_dependencies'), patch.object(k.PRIV, 'ensure'), patch.object(k.PRIV, 'write_files'), patch.object(k.PRIV, 'run', side_effect=privileged), patch.object(k, 'prepare_nvidia_compat', side_effect=lambda: events.append('prepare') or hardware), patch.object(k, 'verify_nvidia_compat', side_effect=lambda _: events.append('verify')), patch.object(k, 'audit_dkms', side_effect=lambda *_: events.append('audit') or (True, False)), patch.object(k, 'prune_stale_modules'), patch.object(k, 'have', return_value=False):
                k.install_packages([], p, '7.4-target')
            self.assertLess(events.index('prepare'), events.index('pacman'))
            if hardware:
                self.assertLess(events.index('audit'), events.index('verify'))
                if 'mkinitcpio' in events:
                    self.assertLess(events.index('verify'), events.index('mkinitcpio'))
            else:
                self.assertNotIn('verify', events)

    def test_passive_power_status_uses_pci_not_drm_card_numbers(self):
        pci = self.root / 'pci'
        proc = self.root / 'proc'
        for address, vendor, cls in [('0004:0a:00.0', '0x10de', '0x030200'), ('0004:0a:00.1', '0x10de', '0x040300'), ('0000:00:02.0', '0x8086', '0x030000')]:
            d = pci / address
            d.mkdir(parents=True)
            (d / 'vendor').write_text(vendor, encoding='utf-8')
            (d / 'class').write_text(cls, encoding='utf-8')
        gpu = pci / '0004:0a:00.0'
        (gpu / 'power').mkdir()
        (gpu / 'power_state').write_text('D3cold\n', encoding='utf-8')
        (gpu / 'power/runtime_status').write_text('suspended\n', encoding='utf-8')
        rows = n.power_status(pci, proc)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]['pci'], '0004:0a:00.0')
        self.assertEqual(rows[0]['power_state'], 'D3cold')
        self.assertEqual(rows[0]['driver'], 'unbound')
        self.assertEqual(rows[0]['nvidia_power'], 'unavailable')


if __name__ == '__main__':
    unittest.main()
