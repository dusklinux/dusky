"""VFIO boot transitions: isolated files/sysfs fixtures, no host writes."""
import contextlib
import importlib.util
import io
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

PATH = Path(__file__).resolve().parents[1] / 'nvidia_vfio_bind_unbind.py'
spec = importlib.util.spec_from_file_location('vfio_tests', PATH)
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)


def device(addr, vendor='10de', klass='030000', driver='nvidia', ident='1234', boot=False):
    return m.common.PciDevice(addr, vendor, ident, klass, driver, boot, 'fixture')


class ToggleTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.addCleanup(patch.stopall)
        for name, path in [('STATE', 'state.json'), ('PHASE_STATE', 'phase.json'), ('MODPROBE', 'modprobe.conf'), ('MODULES', 'modules.conf'), ('CMDLINE_FRAGMENT', 'cmdline.d/vfio.conf')]:
            patch.object(m, name, self.root / path).start()
        m.common.console.file = io.StringIO()
        patch.object(m.common, 'SYS_PCI', self.root / 'pci').start()
        self.boot = self.root / 'boot.conf'
        self.boot.write_text('title fixture\noptions root=UUID=test quiet custom="two words" -- user=unchanged\n')
        self.entry = m.common.BootEntry('type1', self.boot, '', 'fixture.conf')
        self.devices = [device('0000:00:02.0', '8086', driver='i915', boot=True), device('0000:01:00.0'), device('0000:01:00.1', klass='040300', driver='snd_hda_intel', ident='5678')]
        patch.object(m.common, 'enumerate_pci', return_value=self.devices).start()
        patch.object(m, 'foreign_config_check').start()
        patch.object(m, 'active_guest_claims').start()
        patch.object(m, 'check_groups').start()
        patch.object(m.common, 'resolve_boot_entry', return_value=self.entry).start()
        patch.object(m.common, 'preflight', return_value=[]).start()
        patch.object(m, 'validate_modules').start()
        patch.object(m.common, 'cpu_vendor', return_value='intel').start()
        self.args = m.argparse.Namespace(bind=True, unbind=False, slot='0000:01:00', entry=None, allow_boot_vga=False, amd_force_enable=False)

    def test_vendor_driver_selection_preserves_shared_host_drivers(self):
        for vendor, driver in [('10de','nvidia'), ('1002','amdgpu'), ('8086','xe')]:
            with self.subTest(vendor=vendor):
                chosen=device('0000:01:00.0', vendor, driver=driver)
                survivor=device('0000:02:00.0', vendor, driver=driver)
                soft, blocked=m.common.drm_blacklist_for([chosen,survivor],m.common.Claim([chosen]))
                self.assertIn(driver,soft)
                self.assertFalse(blocked)
        claim=m.common.Claim(self.devices[1:])
        _,blocked=m.common.drm_blacklist_for(self.devices,claim)
        self.assertNotIn('snd_hda_intel',blocked)
        self.assertNotIn('i915',blocked)
        self.assertIn('nvidia',blocked)

    def test_full_bind_unbind_roundtrip_retains_quoted_options_and_files(self):
        baseline=self.boot.read_text()
        _,entry,_,desired,files,host_opts,host_files=m.plan(self.args,{})
        m.apply_files(files)
        self.assertIn('custom="two words"',self.boot.read_text())
        self.assertTrue(self.boot.read_text().strip().endswith('-- user=unchanged'))
        self.assertIn('nvidia',desired['module_blacklist'])
        state={'mode':'bind','pending':False,'functions':[{'addr':d.addr,'ids':d.ids} for d in self.devices[1:]],'slots':['0000:01:00'],'host_options':host_opts,'host_files':host_files}
        self.args.bind=False
        *_,files,_,_=m.plan(self.args,state)
        m.apply_files(files)
        self.assertEqual(self.boot.read_text(),baseline)
        self.assertFalse(m.MODPROBE.exists())
        self.assertFalse(m.MODULES.exists())

    def test_phase3_adoption_preserves_unrelated_vfio_ids_and_options(self):
        m.MODPROBE.write_text('# Managed by Arsonix (Phase 3).\noptions vfio-pci ids=10de:1234,10de:5678,abcd:9876 disable_idle_d3=1\nblacklist nvidia\nsoftdep nvidia pre: vfio-pci\n')
        *_,files,host_options,host_files=m.plan(self.args,{})
        text=files[str(m.MODPROBE)]
        self.assertIn('abcd:9876',text)
        self.assertIn('disable_idle_d3=1',text)
        self.assertIn('abcd:9876',host_files[str(m.MODPROBE)])
        self.assertNotIn('10de:1234',host_files[str(m.MODPROBE)])
        self.assertNotIn('blacklist nvidia',host_files[str(m.MODPROBE)])

    def test_quoted_and_duplicate_keys(self):
        text='root=foo custom="two words" vfio_pci.ids=aaaa:bbbb -- vfio-pci.ids=userspace'
        desired={'vfio-pci.ids':'cccc:dddd'}
        merged=m.merge_options(text,desired)
        self.assertIn('custom="two words"',merged)
        self.assertTrue(merged.endswith('-- vfio-pci.ids=userspace'))
        self.assertEqual(m.parameters(merged),desired)
        with self.assertRaisesRegex(RuntimeError,'Duplicate'):
            m.parameters('vfio_pci.ids=aaaa:bbbb vfio-pci.ids=cccc:dddd')

    def test_amd_uses_default_iommu_without_invalid_on_option(self):
        with patch.object(m.common,'cpu_vendor',return_value='amd'):
            _,_,_,desired,*_=m.plan(self.args,{})
            self.assertNotIn('amd_iommu',desired)
            self.args.amd_force_enable=True
            _,_,_,desired,*_=m.plan(self.args,{})
            self.assertEqual(desired['amd_iommu'],'force_enable')

    def test_id_collision_and_boot_gpu_are_rejected(self):
        claim=m.common.Claim([self.devices[1]])
        with self.assertRaises(SystemExit):
            m.common.check_id_collisions([*self.devices,device('0000:02:00.0')],claim)
        with self.assertRaises(SystemExit):
            m.common.guard_claim(m.common.Claim([self.devices[0]]),self.devices,False)

    def test_noop_writes_preserve_inode_mtime_over_1000_reruns(self):
        files={str(self.root/'output'):'sentinel\n'}
        self.assertTrue(m.apply_files(files))
        before=(self.root/'output').stat()
        for _ in range(1000):
            self.assertFalse(m.apply_files(files))
        after=(self.root/'output').stat()
        self.assertEqual((before.st_ino,before.st_mtime_ns),(after.st_ino,after.st_mtime_ns))

    def test_missing_or_corrupted_journal_fails_cleanly(self):
        self.assertEqual(m.saved_state(),{})
        for data in ('{', '[]', '{"schema":9}', '{"schema":1,"mode":"bind","pending":false}'):
            m.STATE.write_text(data)
            with self.subTest(data=data),self.assertRaises((ValueError,RuntimeError)):
                m.saved_state()

    def test_uki_fragment_mode_preserves_unowned_fragments(self):
        folder=self.root/'cmdline.d';folder.mkdir()
        (folder/'root.conf').write_text('root=UUID=keep quiet\n')
        entry=m.common.BootEntry('type2',self.root/'test.efi','root=UUID=keep quiet','test.efi')
        with (patch.object(m.common,'KERNEL_CMDLINE',self.root/'absent'),patch.object(m.common,'VENDOR_CMDLINE',self.root/'also-absent'),patch.object(m.common,'CMDLINE_D',folder)):
            files=m.boot_payload(entry,{'iommu':'pt'})
            m.apply_files(files)
            self.assertEqual((folder/'root.conf').read_text(),'root=UUID=keep quiet\n')
            self.assertEqual(m.CMDLINE_FRAGMENT.read_text(),'iommu=pt\n')
            m.apply_files(m.boot_payload(entry,{}))
            self.assertFalse(m.CMDLINE_FRAGMENT.exists())

    def test_empty_virsh_name_is_skipped(self):
        with (patch.object(m,'active_guest_claims',wraps=ToggleTests.original_active),
              patch.object(m,'checked',return_value='\n') as call):
            m.active_guest_claims(m.common.Claim(self.devices[1:]))
            call.assert_called_once()

    def test_failed_build_is_retryable_and_converged_cli_is_write_free(self):
        lock=self.root/'lock';lock.touch()
        with (patch.object(m,'COMMON_PATH',lock),patch.object(m.common,'check_versions'),
              patch.object(m.common,'elevate'),patch.object(m,'verify_images'),
              patch.object(m.common,'find_boot_entry',return_value=self.entry),
              patch.object(sys,'argv',[str(PATH),'--bind','--slot','0000:01:00']),
              patch.object(m.subprocess,'run',side_effect=m.subprocess.CalledProcessError(1,['mkinitcpio'])) as build,
              contextlib.redirect_stdout(io.StringIO())):
            with self.assertRaises(m.subprocess.CalledProcessError):
                m.main()
            state=m.saved_state()
            self.assertTrue(state['pending'])
            self.assertEqual(state['host_options'],{})
            build.side_effect=None
            self.assertEqual(m.main(),0)
            before=(m.STATE.stat().st_ino,m.STATE.stat().st_mtime_ns)
            for _ in range(20):
                self.assertEqual(m.main(),0)
            self.assertEqual(before,(m.STATE.stat().st_ino,m.STATE.stat().st_mtime_ns))
            self.assertEqual(build.call_count,2)
            self.assertFalse(m.saved_state()['pending'])
            sys.argv=[str(PATH),'--unbind']
            self.assertEqual(m.main(),0)
            self.assertFalse(m.MODPROBE.exists())
            self.assertEqual(m.saved_state()['host_options'],{})

    def test_write_failure_keeps_original_journal_for_retry(self):
        lock=self.root/'lock';lock.touch()
        with (patch.object(m,'COMMON_PATH',lock),patch.object(m.common,'check_versions'),
              patch.object(m.common,'elevate'),patch.object(m,'apply_files',side_effect=OSError('injected full disk')),
              patch.object(sys,'argv',[str(PATH),'--bind','--slot','0000:01:00']),
              contextlib.redirect_stdout(io.StringIO())):
            with self.assertRaisesRegex(OSError,'full disk'):
                m.main()
            self.assertTrue(m.saved_state()['pending'])
            self.assertEqual(m.saved_state()['host_options'],{})
            self.assertNotIn('vfio-pci.ids',self.boot.read_text())

    def test_image_failure_rebuilds_instead_of_getting_stuck(self):
        lock=self.root/'lock';lock.touch()
        with (patch.object(m,'COMMON_PATH',lock),patch.object(m.common,'check_versions'),
              patch.object(m.common,'elevate'),patch.object(m,'verify_images') as verify,
              patch.object(m.common,'find_boot_entry',return_value=self.entry),
              patch.object(m.subprocess,'run') as build,
              patch.object(sys,'argv',[str(PATH),'--bind','--slot','0000:01:00']),
              contextlib.redirect_stdout(io.StringIO())):
            self.assertEqual(m.main(),0)
            verify.side_effect=[RuntimeError('missing config in image'),None]
            self.assertEqual(m.main(),0)
            self.assertEqual(build.call_count,2)

    def test_shared_inspection_accepts_a_distinct_managed_dropin(self):
        config=self.root/'mkinitcpio.conf';config.write_text('MODULES=(btrfs)\nFILES=()\nHOOKS=(base systemd modconf kms)\n')
        dropins=self.root/'dropins';dropins.mkdir()
        managed=dropins/'vfio.conf'
        with (patch.object(m.common,'MKINITCPIO_CONF',config),patch.object(m.common,'MKINITCPIO_DROPIN_DIR',dropins)):
            modules,_,hooks=m.common.effective_config(managed_payload=m.module_payload(),managed_dropin=managed)
            self.assertEqual(modules,['vfio_pci','btrfs'])
            self.assertIn('modconf',hooks)

    original_active=staticmethod(m.active_guest_claims)


if __name__=='__main__':unittest.main()
