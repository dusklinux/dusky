"""Run with python3 -m unittest discover -s user_scripts/gpu/tests -v.

All mutation tests use temporary trees. Image rebuilds, image inspection, and udev reloads are
stubbed in workflow tests; image inspection has separate failure fixtures; Bash config evaluation and udevadm rule verification run for real.
"""
import argparse
import errno
import fcntl
import importlib.util
import io
import json
import random
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from rich.console import Console

SCRIPT = Path(__file__).resolve().parents[1] / 'gpu_disable_toggle.py'

def module(path=SCRIPT):
    spec = importlib.util.spec_from_file_location('gpu_audit', path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    mod.console = Console(file=io.StringIO())
    return mod

class Audit(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.m = module()
        locations = {
            'MODPROBE_FILE': 'etc/modprobe.d/99-gpu-disable.conf',
            'UDEV_RULE': 'etc/udev/rules.d/99-gpu-hide.rules',
            'PRESET_DIR': 'etc/mkinitcpio.d',
            'MKINITCPIO_CONF': 'etc/mkinitcpio.conf',
            'MKINITCPIO_DROPIN_DIR': 'etc/mkinitcpio.conf.d',
            'MKINITCPIO_DROPIN': 'etc/mkinitcpio.conf.d/99-gpu-disable.conf',
            'KERNEL_CMDLINE': 'etc/kernel/cmdline',
            'VENDOR_CMDLINE': 'usr/lib/kernel/cmdline',
            'CMDLINE_D': 'etc/cmdline.d',
            'CMDLINE_D_DROPIN': 'etc/cmdline.d/99-gpu-disable.conf',
            'STATE_DIR': 'var/lib/gpu-disable',
            'STATE_FILE': 'var/lib/gpu-disable/state.json',
            'ASUS_TMPFILES': 'etc/tmpfiles.d/99-asus-dgpu-disable.conf',
            'ASUS_DGPU_DISABLE': 'sys/asus/dgpu_disable',
            'ASUS_ARMOURY_DGPU_DISABLE': 'sys/armoury/current_value',
            'SYS_PCI': 'sys/pci',
        }
        for key, value in locations.items():
            setattr(self.m, key, self.root / value)
        self.write(self.m.MKINITCPIO_CONF, 'MODULES=(i915)\nHOOKS=(base systemd kms)\n')
        self.entry_path = self.root / 'boot/loader/entries/linux.conf'
        self.write(self.entry_path, 'title Linux\nlinux /vmlinuz-linux\ninitrd /initramfs-linux.img\noptions root=UUID=abc quiet iommu=off module_blacklist=other\n')
        self.entry = self.m.BootEntry('type1', self.entry_path, '', 'linux.conf')
        self.preset = self.m.PRESET_DIR / 'linux.preset'
        self.write(self.preset, f'ALL_kver="/boot/vmlinuz-linux"\nPRESETS=(default)\ndefault_image="{self.root}/boot/initramfs-linux.img"\n')
        self.igpu = self.m.PciDevice('0000:00:02.0', '8086', '1234', '030000', 'i915', True, 'iGPU')
        self.gpu = self.m.PciDevice('0000:01:00.0', '10de', 'abcd', '030000', 'nvidia', False, 'dGPU')
        self.audio = self.m.PciDevice('0000:01:00.1', '10de', 'abce', '040300', 'snd_hda_intel', False, 'audio')
        self.devices = [self.igpu, self.gpu, self.audio]
        self.args = argparse.Namespace(slot=None, all=False, auto=True, allow_boot_vga=False, amd_force_enable=False, asus_power_gate=False, yes=True, no_rebuild=False)
        self.m.enumerate_pci = lambda: self.devices
        self.m.find_boot_entry = lambda **kwargs: self.entry
        self.m.warn_modprobe_conflicts = lambda: None
        self.m.reload_udev = lambda: None
        self.m.rebuild_initramfs = lambda **kwargs: None
        self.image_verifier = self.m.verify_images
        self.m.verify_images = lambda *args, **kwargs: None
        self.m.cpu_vendor = lambda: 'intel'

    def write(self, path, text):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)

    def snapshot(self):
        return {str(p.relative_to(self.root)): p.read_bytes() if p.is_file() else None for p in self.root.rglob('*')}

    def disable(self):
        self.m.do_disable(self.args)

    def enable(self):
        self.m.do_enable(self.args)

    def test_roundtrip_restores_baseline(self):
        self.disable()
        self.enable()
        self.assertEqual(self.m.parse_managed_keys(self.m.read_target_options(self.entry)), {'iommu': 'off', 'module_blacklist': 'other'})
        self.assertFalse(self.m.UDEV_RULE.exists())
        self.assertFalse(self.m.MKINITCPIO_DROPIN.exists())
        self.assertEqual(self.m.state_load()['action'], 'enabled')

    def test_disable_preserves_existing_module_blacklist(self):
        self.disable()
        self.assertIn('other', self.m.parse_managed_keys(self.m.read_target_options(self.entry))['module_blacklist'].split(','))

    def test_repeat_visible_preserves_original_snapshot(self):
        self.disable()
        first = self.m.state_load()['preserved_cmdline']
        self.disable()
        self.assertEqual(self.m.state_load()['preserved_cmdline'], first)

    def test_repeat_hidden_targets_original_gpu(self):
        self.disable()
        self.devices = [self.igpu]
        self.disable()
        self.assertEqual(self.m.state_load()['slots'], ['0000:01:00'])
        self.enable()
        self.assertEqual(self.m.state_load()['action'], 'enabled')

    def test_different_target_refused_without_writes(self):
        self.disable()
        before = self.snapshot()
        self.args.slot = '00:02'
        with self.assertRaises(SystemExit):
            self.disable()
        self.assertEqual(before, self.snapshot())

    def test_hardware_swap_refused(self):
        self.disable()
        self.devices = [self.igpu, self.m.PciDevice('0000:01:00.0', '1002', '9999', '030000', 'amdgpu', False, 'new')]
        with self.assertRaises(SystemExit):
            self.disable()

    def test_disable_build_failure_keeps_baseline(self):
        self.m.rebuild_initramfs = lambda **kwargs: self.m.bail('build failed')
        with self.assertRaises(SystemExit):
            self.disable()
        state = self.m.state_load()
        self.assertEqual(state['pending_action'], 'disable')
        self.assertEqual(state['preserved_cmdline']['iommu'], 'off')

    def test_enable_build_failure_can_retry(self):
        self.disable()
        self.m.rebuild_initramfs = lambda **kwargs: self.m.bail('build failed')
        with self.assertRaises(SystemExit):
            self.enable()
        self.assertEqual(self.m.state_load()['preserved_cmdline']['iommu'], 'off')
        self.assertEqual(self.m.state_load()['pending_action'], 'enable')
        self.m.rebuild_initramfs = lambda **kwargs: None
        self.enable()
        self.assertEqual(self.m.state_load()['action'], 'enabled')

    def test_failure_first_config_write_has_recovery(self):
        self.m.write_modprobe = lambda *args, **kwargs: (_ for _ in ()).throw(OSError('write failed'))
        with self.assertRaises(OSError):
            self.disable()
        self.assertEqual(self.m.state_load()['preserved_cmdline']['iommu'], 'off')

    def test_failed_verification_is_fatal(self):
        with self.assertRaises(SystemExit):
            self.m.verify_staged_entry(self.entry, {'iommu': 'pt'})

    def test_disable_dry_run_has_no_filesystem_effects(self):
        self.m.DRY_RUN = True
        before = self.snapshot()
        self.disable()
        self.assertEqual(before, self.snapshot())

    def test_enable_dry_run_has_no_filesystem_effects(self):
        self.disable()
        self.m.DRY_RUN = True
        before = self.snapshot()
        self.enable()
        self.assertEqual(before, self.snapshot())

    def test_no_rebuild_enable_retains_state(self):
        self.disable()
        self.args.no_rebuild = True
        self.enable()
        self.assertEqual(self.m.state_load()['pending_action'], 'enable')
        self.assertEqual(self.m.state_load()['preserved_cmdline']['iommu'], 'off')
        self.args.no_rebuild = False
        self.enable()
        self.assertEqual(self.m.state_load()['action'], 'enabled')

    def test_rule_and_vfio_in_initramfs_configuration(self):
        self.disable()
        text = self.m.MKINITCPIO_DROPIN.read_text()
        self.assertIn(str(self.m.UDEV_RULE), text)
        result = subprocess.run(['bash', '--noprofile', '--norc', '-c', f'source "{self.m.MKINITCPIO_CONF}"; source "{self.m.MKINITCPIO_DROPIN}"; printf "%s\\n" "${{MODULES[@]}}"'], capture_output=True, text=True, check=True)
        self.assertEqual(result.stdout.splitlines()[:2], ['vfio_pci', 'i915'])
        self.assertEqual(subprocess.run(['udevadm', 'verify', str(self.m.UDEV_RULE)], capture_output=True).returncode, 0)

    def test_preflight_rejects_config_override_before_mutations(self):
        self.write(self.preset, self.preset.read_text() + 'ALL_config=/etc/mkinitcpio.conf\n')
        before = self.snapshot()
        with self.assertRaises(SystemExit):
            self.disable()
        self.assertEqual(before, self.snapshot())

    def test_preflight_rejects_wrong_image(self):
        self.write(self.preset, self.preset.read_text().replace('initramfs-linux.img', 'wrong.img'))
        with self.assertRaises(SystemExit):
            self.disable()

    def test_bad_hooks_rejected_before_writes(self):
        self.write(self.m.MKINITCPIO_CONF, 'HOOKS=(base)\n')
        before = self.snapshot()
        with self.assertRaises(SystemExit):
            self.disable()
        self.assertEqual(before, self.snapshot())

    def test_same_vendor_surviving_gpu_not_blacklisted(self):
        survivor = self.m.PciDevice('0000:02:00.0', '10de', 'ffff', '030000', 'nvidia', False, 'other')
        _, blacklist = self.m.drm_blacklist_for(self.devices + [survivor], self.m.Claim([self.gpu, self.audio]))
        self.assertNotIn('nvidia', blacklist)

    def test_shared_transport_driver_not_blacklisted(self):
        gpu = self.m.PciDevice('0000:02:00.0', '1af4', '1050', '030000', 'virtio-pci', False, 'GPU')
        disk = self.m.PciDevice('0000:03:00.0', '1af4', '1042', '010000', 'virtio-pci', False, 'disk')
        _, blacklist = self.m.drm_blacklist_for([gpu, disk], self.m.Claim([gpu]))
        self.assertNotIn('virtio_pci', blacklist)

    def test_id_collision_detected(self):
        duplicate = self.m.PciDevice('0000:02:00.0', '10de', 'abcd', '030000', 'nvidia', False, 'duplicate')
        with self.assertRaises(SystemExit):
            self.m.check_id_collisions(self.devices + [duplicate], self.m.Claim([self.gpu]))

    def test_last_gpu_guard(self):
        with self.assertRaises(SystemExit):
            self.m.guard_claim(self.m.Claim([self.igpu]), [self.igpu], False)

    def test_cmdline_quotes_preserved(self):
        self.assertEqual(self.m.merge_cmdline('root=UUID=abc foo="a b" iommu=off -- initarg="c d"', {'iommu': 'pt'}), 'root=UUID=abc foo="a b" iommu=pt -- initarg="c d"')

    def test_cmdline_init_parameters_ignored(self):
        self.assertEqual(self.m.parse_managed_keys('iommu=pt -- iommu=off'), {'iommu': 'pt'})
        self.assertEqual(self.m.merge_cmdline('root=x -- -- thing', {}), 'root=x -- -- thing')

    def test_duplicate_parameters_rejected(self):
        with self.assertRaises(SystemExit):
            self.m.parse_managed_keys('iommu=off iommu=pt')

    def test_unbalanced_quotes_rejected(self):
        with self.assertRaises(SystemExit):
            self.m.cmdline_tokens('foo="bad')

    def test_multiple_options_lines_merged(self):
        self.write(self.entry_path, self.entry_path.read_text() + 'options foo="a b"\n')
        self.disable()
        text = self.entry_path.read_text()
        self.assertEqual(sum((line.startswith('options ') for line in text.splitlines())), 1)
        self.assertIn('foo="a b"', text)

    def test_corrupt_state_rejected(self):
        for text in ('{', '[]', '{"ids":null}', '{"preserved_cmdline":[]}', '{"bridges":[]}'):
            with self.subTest(text=text):
                self.write(self.m.STATE_FILE, text)
                with self.assertRaises(SystemExit):
                    self.m.state_load()

    def test_bad_function_metadata_rejected(self):
        for item in ({'addr': 'bad', 'ids': 'abc:def', 'klass4': '0300'}, None):
            with self.assertRaises(SystemExit):
                self.m.claim_from_state({'functions': [item]})

    def test_legacy_ids_not_guessed(self):
        with self.assertRaises(SystemExit):
            self.m.claim_from_state({'addrs': ['0000:01:00.0'], 'ids': ['10de:abcd']})

    def test_enable_without_config_is_noop(self):
        before = self.snapshot()
        self.enable()
        self.assertEqual(before, self.snapshot())

    def test_asus_disable_stages_without_live_write(self):
        self.write(self.m.ASUS_DGPU_DISABLE, '0')
        self.args.asus_power_gate = True
        self.disable()
        self.assertEqual(self.m.ASUS_DGPU_DISABLE.read_text(), '0')
        self.assertTrue(self.m.ASUS_TMPFILES.exists())
        self.write(self.m.ASUS_DGPU_DISABLE, '1')
        self.enable()
        self.assertEqual(self.m.ASUS_DGPU_DISABLE.read_text(), '0')

    def test_asus_untouched_without_optin(self):
        self.write(self.m.ASUS_DGPU_DISABLE, '1')
        self.disable()
        self.enable()
        self.assertEqual(self.m.ASUS_DGPU_DISABLE.read_text(), '1')

    def test_uki_fragment_does_not_copy_foreign_options(self):
        self.write(self.m.CMDLINE_D / '10-root.conf', 'root=UUID=abc quiet\n')
        entry = self.m.BootEntry('type2', self.root / 'boot/EFI/Linux/linux.efi', 'root=UUID=abc quiet', 'linux.efi')
        self.m.patch_bootloader(entry, {'nvidia'}, ['10de:abcd'], 'intel', False, enable=False)
        self.assertFalse(self.m.KERNEL_CMDLINE.exists())
        self.assertNotIn('root=', self.m.CMDLINE_D_DROPIN.read_text())
        self.m.patch_bootloader(entry, set(), [], 'intel', False, enable=True)
        self.assertFalse(self.m.CMDLINE_D_DROPIN.exists())

    def test_uki_existing_cmdline_does_not_copy_fragments(self):
        self.write(self.m.KERNEL_CMDLINE, 'root=UUID=abc quiet\n')
        self.write(self.m.CMDLINE_D / '10-console.conf', 'console=tty0\n')
        entry = self.m.BootEntry('type2', self.root / 'boot/EFI/Linux/linux.efi', '', 'linux.efi')
        for _ in range(2):
            self.m.patch_bootloader(entry, {'nvidia'}, ['10de:abcd'], 'intel', False, enable=False)
        self.assertNotIn('console=', self.m.KERNEL_CMDLINE.read_text())
        self.assertEqual(self.m.read_target_options(entry).count('console=tty0'), 1)

    def test_atomic_write_is_idempotent_and_preserves_mode(self):
        path = self.root / 'file'
        self.write(path, 'old')
        path.chmod(0o640)
        self.assertTrue(self.m.atomic_write(path, 'new'))
        self.assertFalse(self.m.atomic_write(path, 'new'))
        self.assertEqual(path.stat().st_mode & 0o777, 0o640)

    def test_verification_stays_on_original_entry(self):
        self.disable()
        expected = self.m.parse_managed_keys(self.m.read_target_options(self.entry))
        seen = []

        def lookup(**kwargs):
            seen.append(kwargs.get('ident'))
            return self.entry if kwargs.get('ident') == 'linux.conf' else None
        self.m.find_boot_entry = lookup
        self.m.verify_staged_entry(self.entry, expected)
        self.assertEqual(seen, ['linux.conf'])

    def test_bootctl_json_uses_pinned_entry_over_changed_default(self):
        actual = module()
        other = self.root / 'boot/loader/entries/other.conf'
        self.write(other, 'options root=other\n')
        entries = [{'type': 'type1', 'path': str(other), 'id': 'other.conf', 'isDefault': True}, {'type': 'type1', 'path': str(self.entry_path), 'id': 'linux.conf', 'isDefault': False}]
        actual.run = lambda argv: subprocess.CompletedProcess(argv, 0, json.dumps(entries), '')
        self.assertEqual(actual.find_boot_entry(quiet=True, ident='linux.conf').path, self.entry_path)
        self.assertEqual(actual.find_boot_entry(quiet=True).path, other)

    def test_bootctl_ambiguous_entries_not_guessed(self):
        actual = module()
        entries = [{'type': 'type1', 'id': 'one.conf'}, {'type': 'type1', 'id': 'two.conf'}]
        actual.run = lambda argv: subprocess.CompletedProcess(argv, 0, json.dumps(entries), '')
        self.assertIsNone(actual.find_boot_entry(quiet=True))

    def test_preflight_uki_target_is_generated(self):
        uki = self.root / 'boot/EFI/Linux/linux.efi'
        self.write(uki, 'fixture')
        self.write(self.preset, self.preset.read_text() + f'default_uki="{uki}"\n')
        entry = self.m.BootEntry('type2', uki, 'root=UUID=abc', 'linux.efi')
        self.m.preflight(entry, enable=False)
        self.write(self.preset, self.preset.read_text() + 'ALL_cmdline=/custom/cmdline\n')
        with self.assertRaises(SystemExit):
            self.m.preflight(entry, enable=False)

    def test_missing_generator_rejected_before_writes(self):
        before = self.snapshot()
        with patch.object(self.m.shutil, 'which', return_value=None):
            with self.assertRaises(SystemExit):
                self.disable()
        self.assertEqual(before, self.snapshot())

    def test_concurrent_operation_refused(self):
        before = self.snapshot()
        with SCRIPT.open('rb') as held:
            fcntl.flock(held, fcntl.LOCK_EX | fcntl.LOCK_NB)
            with patch.object(self.m, 'elevate'), patch.object(sys, 'argv', ['gpu', '--disable', '--auto', '--dry-run']):
                with self.assertRaises(SystemExit):
                    self.m.main()
        self.assertEqual(before, self.snapshot())

    def test_asus_failed_enable_restoration_can_retry(self):
        self.disable()
        self.write(self.m.ASUS_TMPFILES, 'legacy gate\n')
        self.write(self.m.ASUS_DGPU_DISABLE, '1')
        original = Path.write_text

        def fail_gate(path, *args, **kwargs):
            if path == self.m.ASUS_DGPU_DISABLE:
                raise OSError('firmware write failed')
            return original(path, *args, **kwargs)
        with patch.object(Path, 'write_text', fail_gate):
            with self.assertRaises(OSError):
                self.enable()
        self.assertTrue(self.m.state_load()['asus_power_gate'])
        self.enable()
        self.assertEqual(self.m.ASUS_DGPU_DISABLE.read_text(), '0')
    def test_mkinitcpio_uses_version_order(self):
        self.write(self.m.MKINITCPIO_DROPIN_DIR / '99-base.conf', 'HOOKS=(systemd)\n')
        self.write(self.m.MKINITCPIO_DROPIN_DIR / '100-final.conf', 'HOOKS=(udev)\n')
        self.assertEqual(self.m.effective_hooks(), ['udev'])

    def test_later_dropin_overrides_rejected_before_mutation(self):
        self.write(self.m.MKINITCPIO_DROPIN_DIR / '100-final.conf', 'MODULES=(i915)\nFILES=()\n')
        before = self.snapshot()
        with self.assertRaises(SystemExit):
            self.disable()
        self.assertEqual(before, self.snapshot())

    def test_missing_baseline_is_not_replaced_with_our_output(self):
        self.disable()
        state = self.m.state_load()
        del state['preserved_cmdline']
        self.write(self.m.STATE_FILE, json.dumps(state))
        before = self.snapshot()
        with self.assertRaises(SystemExit):
            self.disable()
        self.assertEqual(before, self.snapshot())

    def test_new_function_in_saved_slot_rejected(self):
        self.disable()
        self.devices.append(self.m.PciDevice('0000:01:00.2', '10de', 'abcd', '0c0300', 'xhci_pci', False, 'new USB'))
        before = self.snapshot()
        with self.assertRaises(SystemExit):
            self.disable()
        self.assertEqual(before, self.snapshot())

    def test_firmware_attribute_missing_keeps_recovery(self):
        self.write(self.m.ASUS_DGPU_DISABLE, '0')
        self.args.asus_power_gate = True
        self.disable()
        self.m.ASUS_DGPU_DISABLE.unlink()
        with self.assertRaises(SystemExit):
            self.enable()
        self.assertEqual(self.m.state_load()['pending_action'], 'enable')
        self.write(self.m.ASUS_DGPU_DISABLE, '1')
        self.enable()
        self.assertEqual(self.m.ASUS_DGPU_DISABLE.read_text(), '0')

    def test_image_file_presence_checked_for_both_actions(self):
        expected = '\n'.join(str(p).lstrip('/') for p in (self.m.UDEV_RULE, self.m.MODPROBE_FILE))
        image = self.root / 'image.img'
        for enable, listing, success in ((False, expected, True), (False, '', False),
                                         (True, expected, False), (True, '', True)):
            with self.subTest(enable=enable, success=success):
                self.m.run = lambda argv, **kwargs: subprocess.CompletedProcess(argv, 0, listing, '')
                if success:
                    self.image_verifier([image], enable=enable)
                else:
                    with self.assertRaises(SystemExit):
                        self.image_verifier([image], enable=enable)
        self.m.run = lambda argv, **kwargs: subprocess.CompletedProcess(argv, 1, '', 'corrupt image')
        with self.assertRaises(SystemExit):
            self.image_verifier([image], enable=False)

    def test_image_verification_failure_keeps_disable_state(self):
        self.m.verify_images = lambda *args, **kwargs: self.m.bail('wrong image')
        with self.assertRaises(SystemExit):
            self.disable()
        self.assertEqual(self.m.state_load()['pending_action'], 'disable')
        self.assertEqual(self.m.state_load()['preserved_cmdline']['iommu'], 'off')

    def test_io_errors_not_ignored_on_directory_sync(self):
        for error, supported in ((errno.EIO, True), (errno.ENOSPC, True), (errno.EINVAL, False)):
            with self.subTest(error=error):
                with patch.object(self.m.os, 'fsync', side_effect=OSError(error, 'fixture')):
                    if supported:
                        with self.assertRaises(OSError):
                            self.m.sync_directory(self.root)
                    else:
                        self.m.sync_directory(self.root)

    def test_failed_atomic_replace_keeps_original_and_cleans_temp(self):
        path = self.root / 'atomic'
        self.write(path, 'original')
        for operation in ('replace', 'fchmod'):
            with self.subTest(operation=operation):
                with patch.object(self.m.os, operation, side_effect=OSError(errno.EIO, 'fixture')):
                    with self.assertRaises(OSError):
                        self.m.atomic_write(path, 'changed')
                self.assertEqual(path.read_text(), 'original')
                self.assertEqual(list(self.root.glob('.atomic.*')), [])

    def test_persistent_rescue_flags_preserved(self):
        current = 'root=ok systemd.unit=rescue.target initrd=/chosen.img'
        self.assertEqual(self.m.merge_cmdline(current, {}), current)
        self.assertEqual(self.m.merge_cmdline(current, {}, running_seed=True), 'root=ok')

    def test_indented_boot_options_roundtrip(self):
        self.write(self.entry_path, self.entry_path.read_text().replace('options ', '  options ').replace('initrd ', '  initrd '))
        self.disable()
        self.enable()
        self.assertIn('root=UUID=abc', self.m.read_target_options(self.entry))
        self.assertEqual(self.m.parse_managed_keys(self.m.read_target_options(self.entry))['iommu'], 'off')

    def test_state_field_type_matrix(self):
        for key in ('action', 'pending_action', 'asus_power_gate', 'rebuilt', 'asus_restore', 'boot_id'):
            for value in ([], {}, 42):
                with self.subTest(key=key, value=value):
                    self.write(self.m.STATE_FILE, json.dumps({key: value}))
                    with self.assertRaises(SystemExit):
                        self.m.state_load()

    def test_command_runner_missing_timeout_and_invalid_utf8(self):
        actual = module()
        self.assertEqual(actual.run(['/no/such/gpu-test-command']).returncode, 127)
        result = actual.run([sys.executable, '-c', 'import time; time.sleep(1)'], timeout=0.01)
        self.assertEqual(result.returncode, 124)
        result = actual.run([sys.executable, '-c', "import os; os.write(1, bytes([255]))"])
        self.assertEqual(result.returncode, 0)
        self.assertEqual(result.stdout, '\ufffd')

    def test_two_thousand_random_cmdline_roundtrips(self):
        rng = random.Random(20261010)
        for index in range(2000):
            value = ' '.join(str(rng.randrange(1000)) for _ in range(rng.randrange(1, 5)))
            current = f'root=UUID={index} label="{value}" iommu=off -- iommu=initarg --'
            desired = {'iommu': rng.choice(('pt', 'off'))}
            merged = self.m.merge_cmdline(current, desired)
            self.assertEqual(self.m.parse_managed_keys(merged), desired)
            self.assertIn(f'label="{value}"', merged)
            self.assertTrue(merged.endswith('-- iommu=initarg --'))
            self.assertEqual(self.m.merge_cmdline(merged, desired), merged)

    def test_fifty_complete_cycles(self):
        for _ in range(50):
            self.disable()
            self.enable()
            self.assertEqual(self.m.state_load()['action'], 'enabled')
            self.assertEqual(self.m.parse_managed_keys(self.m.read_target_options(self.entry)),
                             {'iommu': 'off', 'module_blacklist': 'other'})

    def test_selection_and_domain_ambiguity(self):
        self.assertEqual(self.m.normalize_slot('01:00.1', ['0000:01:00']), '0000:01:00')
        self.assertEqual(self.m.normalize_slot('000A:01:00.0', ['000a:01:00']), '000a:01:00')
        self.assertIsNone(self.m.normalize_slot('01:00', ['0000:01:00', '0001:01:00']))
        for invalid in ('', '.', '0', '01', '01:00.9', 'garbage'):
            self.assertIsNone(self.m.normalize_slot(invalid, ['0000:01:00']))
        self.args.all = True
        self.assertEqual(self.m.select_claim(self.devices, self.m.gpu_slots(self.devices), self.args).addrs,
                         [self.gpu.addr, self.audio.addr])
        self.args.all = False
        self.args.slot = ''
        with self.assertRaises(SystemExit):
            self.disable()

    def test_sysfs_discovery_and_optional_labels(self):
        actual = module()
        actual.SYS_PCI = self.m.SYS_PCI
        actual.lspci_labels = lambda: {}
        for device in self.devices:
            node = actual.SYS_PCI / device.addr
            self.write(node / 'vendor', '0x' + device.vendor)
            self.write(node / 'device', '0x' + device.device)
            self.write(node / 'class', '0x' + device.klass)
            self.write(node / 'boot_vga', '1' if device.boot_vga else '0')
            driver = self.root / 'drivers' / device.driver
            driver.mkdir(parents=True, exist_ok=True)
            (node / 'driver').symlink_to(driver)
        (actual.SYS_PCI / '0000:02:00.0').mkdir()
        found = actual.enumerate_pci()
        self.assertEqual([d.addr for d in found], [d.addr for d in self.devices])
        self.assertEqual(found[0].driver, 'i915')
        self.assertTrue(found[0].boot_vga)

    def test_cli_rejects_incompatible_flags_before_elevation(self):
        for flags in (['--status', '--slot='], ['--enable', '--slot='], ['--enable', '--asus-power-gate'],
                      ['--status', '--dry-run'], ['--disable', '--enable']):
            with self.subTest(flags=flags), patch.object(sys, 'argv', ['gpu', *flags]), patch.object(self.m, 'elevate') as elevate:
                with self.assertRaises(SystemExit) as raised:
                    self.m.main()
                self.assertEqual(raised.exception.code, 2)
                elevate.assert_not_called()

    def test_write_failure_matrix_and_retry(self):
        # Each disable mutation: recovery journal, modprobe, boot entry,
        # initramfs drop-in, hide rule, completion journal. Fail before and
        # after each atomic write, then retry and release the exact baseline.
        for target in range(1, 7):
            for after in (False, True):
                with self.subTest(target=target, after=after):
                    case = Audit()
                    case.setUp()
                    try:
                        original = case.m.atomic_write
                        count = 0
                        def fail_at(path, content):
                            nonlocal count
                            count += 1
                            if count == target and not after:
                                raise OSError(errno.ENOSPC, 'fixture full disk')
                            result = original(path, content)
                            if count == target:
                                raise OSError(errno.EIO, 'fixture write completion failure')
                            return result
                        case.m.atomic_write = fail_at
                        with self.assertRaises(OSError):
                            case.disable()
                        case.m.atomic_write = original
                        case.disable()
                        case.enable()
                        case.assertEqual(case.m.parse_managed_keys(case.m.read_target_options(case.entry)),
                                         {'iommu': 'off', 'module_blacklist': 'other'})
                    finally:
                        case.doCleanups()

    def test_enable_deletion_failure_matrix_and_retry(self):
        for target in range(1, 4):
            for after in (False, True):
                with self.subTest(target=target, after=after):
                    case = Audit()
                    case.setUp()
                    try:
                        case.disable()
                        original = case.m.remove_file
                        count = 0
                        def fail_at(path):
                            nonlocal count
                            count += 1
                            if count == target and not after:
                                raise OSError(errno.EIO, 'fixture deletion failure')
                            original(path)
                            if count == target:
                                raise OSError(errno.EIO, 'fixture directory sync failure')
                        case.m.remove_file = fail_at
                        with self.assertRaises(OSError):
                            case.enable()
                        case.assertEqual(case.m.state_load()['preserved_cmdline']['iommu'], 'off')
                        case.m.remove_file = original
                        case.enable()
                        case.assertEqual(case.m.state_load()['action'], 'enabled')
                    finally:
                        case.doCleanups()

    def test_unchanged_write_still_reports_sync_failure(self):
        path = self.root / 'atomic'
        self.write(path, 'original')
        with patch.object(self.m.os, 'fsync', side_effect=OSError(errno.EIO, 'fixture')):
            with self.assertRaises(OSError):
                self.m.atomic_write(path, 'original')
        self.assertEqual(path.read_text(), 'original')

    def test_udev_cache_reloaded_on_idempotent_retry(self):
        self.disable()
        with patch.object(self.m, 'reload_udev') as reload:
            self.m.write_udev_hide(self.m.Claim([self.gpu, self.audio]))
            reload.assert_called_once()
        self.m.UDEV_RULE.unlink()
        with patch.object(self.m, 'reload_udev') as reload:
            self.m.remove_udev_hide()
            reload.assert_called_once()

    def test_lock_exclusion_across_processes(self):
        code = """import importlib.util,sys
spec=importlib.util.spec_from_file_location('lock_probe',sys.argv[1])
m=importlib.util.module_from_spec(spec);sys.modules[spec.name]=m;spec.loader.exec_module(m)
m.elevate=lambda: None
sys.argv=['gpu','--disable','--auto','--dry-run']
m.main()
"""
        with SCRIPT.open('rb') as held:
            fcntl.flock(held, fcntl.LOCK_EX | fcntl.LOCK_NB)
            proc = subprocess.run([sys.executable, '-B', '-c', code, str(SCRIPT)], text=True,
                                  capture_output=True, timeout=10, check=False)
        self.assertEqual(proc.returncode, 1)
        self.assertIn('Another GPU toggle operation is running', proc.stdout)

    def test_existing_vfio_ids_match_modprobe_and_boot_options(self):
        self.write(self.entry_path, self.entry_path.read_text().rstrip() + ' vfio-pci.ids=1234:5678\n')
        self.disable()
        ids = self.m.parse_managed_keys(self.m.read_target_options(self.entry))['vfio-pci.ids']
        self.assertIn('1234:5678', ids)
        self.assertIn('options vfio-pci ids=' + ids, self.m.MODPROBE_FILE.read_text())
        self.enable()
        self.assertEqual(self.m.parse_managed_keys(self.m.read_target_options(self.entry))['vfio-pci.ids'],
                         '1234:5678')

    def test_effective_config_preserves_source_file_identity(self):
        self.write(self.m.MKINITCPIO_CONF,
                   'source "${BASH_SOURCE[0]%/*}/arrays.conf"\n')
        self.write(self.m.MKINITCPIO_CONF.parent / 'arrays.conf',
                   'MODULES=(i915)\nFILES=(/fixture)\nHOOKS=(base systemd)\n')
        self.assertEqual(self.m.effective_config(),
                         (['i915'], ['/fixture'], ['base', 'systemd']))

    def test_elevation_keeps_exact_interpreter_and_arguments(self):
        with patch.object(self.m.os, 'geteuid', return_value=1000), \
             patch.object(self.m.shutil, 'which', return_value='/usr/bin/sudo'), \
             patch.object(self.m.os, 'execv') as execv, \
             patch.object(sys, 'argv', [str(SCRIPT), '--disable', '--dry-run']):
            self.m.elevate()
            execv.assert_called_once_with('/usr/bin/sudo',
                ['/usr/bin/sudo', '--', sys.executable, str(SCRIPT), '--disable', '--dry-run'])

if __name__ == '__main__':
    unittest.main(verbosity=2)
