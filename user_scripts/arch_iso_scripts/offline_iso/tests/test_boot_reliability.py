"""Non-destructive regression tests: all filesystem writes use temporary fixtures."""
import importlib.util
from pathlib import Path
import shlex
import subprocess
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
HOME = ROOT.parents[2]


def load(name, filename):
    spec = importlib.util.spec_from_file_location(name, ROOT / filename)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def shell_functions(path):
    return path.read_text().rsplit('\nmain', 1)[0]


class BootReliability(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.boot = load('boot_fixture', '151_systemd_bootloader.py')
        cls.strap = load('strap_fixture', '070_pacstrap_and_disable_mkinitcpio.py')
        cls.repos = load('repo_fixture', '051_pacman_repo_switch.py')

    def test_command_lines_keep_esp_checks(self):
        for filesystem in ('btrfs', 'ext4'):
            for encrypted in (False, True):
                topo = dict(ROOT_FSTYPE=filesystem, ROOT_UUID='root-uuid', ROOT_SUBVOL='/@')
                luks = dict(found=encrypted, BACKING_DEV='/dev/fixture',
                            LUKS_UUID='luks-uuid', MAPPER_NAME='cryptroot')
                primary, fallback, _ = self.boot.build_cmdlines(topo, luks, ' sd-encrypt ')
                for line in (primary, fallback):
                    self.assertNotIn('fsck.mode=skip', line)
                    self.assertIn('root=UUID=root-uuid', line)
                self.assertEqual('rd.luks.name=luks-uuid=cryptroot' in primary, encrypted)

    def test_pacman_lock_timeout_preserves_lock(self):
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory)
            lock = target / 'var/lib/pacman/db.lck'
            lock.parent.mkdir(parents=True)
            lock.write_text('owned by transaction')
            with patch.object(self.strap, 'MOUNT_POINT', target):
                with self.assertRaises(TimeoutError):
                    self.strap.wait_for_pacman_lock(timeout=0)
            self.assertEqual(lock.read_text(), 'owned by transaction')

    def test_pacman_waits_for_transaction(self):
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory)
            lock = target / 'var/lib/pacman/db.lck'
            lock.parent.mkdir(parents=True)
            lock.touch()
            timer = threading.Timer(0.1, lock.unlink)
            timer.start()
            try:
                with patch.object(self.strap, 'MOUNT_POINT', target):
                    self.strap.wait_for_pacman_lock(timeout=2)
            finally:
                timer.join()
            self.assertFalse(lock.exists())

    def test_package_manifest_contains_realtime_provider(self):
        result = subprocess.run(['bash', str(ROOT / '130_chroot_package_installer.sh'),
                                 '--list-packages'], capture_output=True, text=True, check=True)
        packages = result.stdout.splitlines()
        self.assertIn('rtkit', packages)
        self.assertEqual(len(packages), len(set(packages)))

    def test_autologin_generation_and_old_format_recognition(self):
        source = shell_functions(ROOT / '115_tty_autologin.sh')
        with tempfile.TemporaryDirectory() as directory:
            fixture = Path(directory) / 'override.conf'
            source = source.replace('readonly OVERRIDE_FILE="${SYSTEMD_DIR}/override.conf"',
                                    'readonly OVERRIDE_FILE=' + shlex.quote(str(fixture)))
            for account in ('alice', 'user-name', 'user_name'):
                new = subprocess.run(['bash', '-c', source + '\noverride_content "$1"',
                                      'test', account], capture_output=True, text=True, check=True)
                self.assertIn('--noreset --noclear --noissue -- - ${TERM}', new.stdout)
                fixture.write_text(new.stdout)
                subprocess.run(['bash', '-c', source + '\nmanaged_override_exists'], check=True)
                fixture.write_text('[Service]\nExecStart=\nExecStart=-/usr/bin/agetty --autologin '
                                   + account + ' --noclear --noissue %I $TERM\n')
                subprocess.run(['bash', '-c', source + '\nmanaged_override_exists'], check=True)

    def test_battery_waits_for_delayed_hardware(self):
        limiter = HOME / 'user_scripts/battery/limiter/battery_limiter.sh'
        source = shell_functions(limiter)
        result = subprocess.run(['bash', '-c', source + '\nwrite_service 80'],
                                capture_output=True, text=True, check=True)
        self.assertIn('StartLimitIntervalSec=120', result.stdout)
        # Apply the documented systemd percent unescaping and line continuation.
        unit = result.stdout.replace('\\\n', '').replace('%%', '%')
        command = unit.split('ExecStart=:', 1)[1].split('\n\n[Install]', 1)[0].strip()
        argv = shlex.split(command)
        with tempfile.TemporaryDirectory() as directory:
            fixture = Path(directory)
            script = argv[argv.index('-c') + 1].replace('/sys/class/power_supply', directory)
            supply = fixture / 'arbitrary-battery-name'
            supply.mkdir()
            threshold = supply / 'charge_control_end_threshold'
            timer = threading.Timer(0.1, lambda: threshold.write_text('100\n'))
            timer.start()
            try:
                subprocess.run(['bash', '-c', script, 'battery-charge-limit', '80'],
                               timeout=4, check=True)
            finally:
                timer.join()
            self.assertEqual(threshold.read_text(), '80\n')
            # Multiple supplies, including the alternate threshold interface.
            second = fixture / 'charger'
            second.mkdir()
            (second / 'stop_charge_thresh').write_text('50\n')
            for _ in range(20):
                subprocess.run(['bash', '-c', script, 'battery-charge-limit', '80'],
                               timeout=2, check=True)
            self.assertEqual((second / 'stop_charge_thresh').read_text(), '80\n')

    def test_failed_initramfs_build_rejects_stale_images(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for name in ('boot', 'etc/mkinitcpio.d', 'etc/pacman.d/hooks', 'usr/lib/modules/test', 'bin'):
                (root / name).mkdir(parents=True, exist_ok=True)
            (root / 'usr/lib/modules/test/pkgbase').write_text('linux')
            (root / 'usr/lib/modules/test/vmlinuz').write_text('fixture-kernel')
            (root / 'boot/initramfs-linux.img').write_text('stale-image')
            command = root / 'bin/mkinitcpio'
            command.write_text('#!/bin/sh\nexit 7\n')
            command.chmod(0o755)
            source = (ROOT / '150_mkinitcpio_restore_and_generate.sh').read_text()
            for prefix in ('/usr/lib/modules', '/etc/', '/boot/'):
                source = source.replace(prefix, str(root) + prefix)
            import os
            result = subprocess.run(['bash', '-c', source], capture_output=True, text=True,
                                    env=os.environ | {'PATH': str(root / 'bin') + ':' + os.environ['PATH']})
            self.assertEqual(result.returncode, 7, result.stdout + result.stderr)

    def test_failed_bootctl_update_is_not_accepted(self):
        with tempfile.TemporaryDirectory() as directory, \
             patch.object(self.boot, 'ESP_MNT', Path(directory)), \
             patch.object(self.boot, 'get_kernels', return_value=[(Path(directory), 'linux')]), \
             patch.object(self.boot, 'ensure_esp'), patch.object(self.boot, 'ensure_udev_bind_mount_in_chroot'):
            (Path(directory)/'initramfs-linux.img').write_text('fixture-image')
            def command(*argv, **kwargs):
                if argv[:2] == ('bootctl', 'update'):
                    self.assertTrue(kwargs.get('check', True))
                    raise subprocess.CalledProcessError(1, argv)
                return subprocess.CompletedProcess(argv, 0, '', '')
            with patch.object(self.boot, 'run', side_effect=command):
                with self.assertRaises(subprocess.CalledProcessError):
                    self.boot.install_systemd_boot_uefi('fixture-primary', 'fixture-fallback')

    def test_bls_entries_only_reference_existing_images(self):
        for fallback_present in (False, True):
            with tempfile.TemporaryDirectory() as directory:
                esp = Path(directory)
                kernel = esp / 'modules/fixture'
                kernel.mkdir(parents=True)
                (kernel / 'vmlinuz').write_text('kernel')
                efi = esp / 'EFI/systemd/systemd-bootx64.efi'
                efi.parent.mkdir(parents=True)
                efi.write_text('efi')
                (esp / 'initramfs-linux.img').write_text('main-image')
                (esp / 'loader/entries').mkdir(parents=True)
                (esp / 'loader/entries/arch-personal.conf').write_text('title Personal entry\n')
                if fallback_present:
                    (esp / 'initramfs-linux-fallback.img').write_text('fallback-image')
                with patch.multiple(self.boot, ESP_MNT=esp, LOADER_CONF=esp/'loader/loader.conf',
                                    ENTRIES_DIR=esp/'loader/entries'), \
                     patch.object(self.boot, 'ensure_esp'), \
                     patch.object(self.boot, 'ensure_udev_bind_mount_in_chroot'), \
                     patch.object(self.boot, 'get_kernels', return_value=[(kernel, 'linux')]), \
                     patch.object(self.boot, 'run', side_effect=lambda *a, **k: subprocess.CompletedProcess(a, 0, '', '')):
                    self.boot.install_systemd_boot_uefi('root=fixture', 'root=fixture')
                for entry in (esp/'loader/entries').glob('arch-*.conf'):
                    for line in entry.read_text().splitlines():
                        if line.startswith(('linux ', 'initrd ')):
                            self.assertTrue((esp / line.split()[1].lstrip('/')).is_file())
                self.assertEqual((esp/'loader/entries/arch-linux-fallback.conf').exists(), fallback_present)
                self.assertEqual((esp/'loader/entries/arch-personal.conf').read_text(), 'title Personal entry\n')

    def test_missing_offline_media_keeps_configuration(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            conf, mirrors = root/'pacman.conf', root/'mirrorlist'
            conf.write_text('original')
            mirrors.write_text('original mirrors')
            with patch.object(self.repos, 'get_offline_repo_path', return_value=(root/'missing').as_uri()):
                with self.assertRaises(FileNotFoundError):
                    self.repos.switch_to_offline(conf, mirrors)
            self.assertEqual(conf.read_text(), 'original')
            self.assertEqual(mirrors.read_text(), 'original mirrors')

    def test_repository_sync_failure_propagates(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            with patch.object(self.repos, 'wait_for_pacman_lock'), \
                 patch.object(self.repos.subprocess, 'run', return_value=subprocess.CompletedProcess([], 44, 'sync failed')):
                with self.assertRaises(subprocess.CalledProcessError):
                    self.repos.switch_to_online(root/'pacman.conf', root/'mirrorlist')

    def test_repository_busy_does_not_publish_configuration(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            with patch.object(self.repos, 'wait_for_pacman_lock', side_effect=TimeoutError):
                with self.assertRaises(TimeoutError):
                    self.repos.switch_to_online(root/'pacman.conf', root/'mirrorlist')
            self.assertFalse((root/'pacman.conf').exists())

    def test_encryption_detection_error_is_not_plain_root(self):
        with patch.object(self.boot, 'run', side_effect=subprocess.CalledProcessError(1, ['lsblk'])):
            with self.assertRaises(RuntimeError):
                self.boot.detect_luks('/dev/fixture')

    def test_root_topology_requires_complete_metadata(self):
        import json
        for record in ({'source': '/dev/fixture', 'uuid': None, 'fstype': 'btrfs'},
                       {'source': '/dev/fixture', 'uuid': 'fixture', 'fstype': None}):
            result = subprocess.CompletedProcess([], 0, json.dumps({'filesystems': [record]}))
            with patch.object(self.boot, 'run', return_value=result):
                with self.assertRaises(RuntimeError):
                    self.boot.get_root_topology()

    def test_boot_config_replace_failure_preserves_existing_file(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)/'arch-linux.conf'
            path.write_text('original-entry')
            with patch.object(Path, 'replace', side_effect=OSError('fixture publication failure')):
                with self.assertRaises(OSError):
                    self.boot.write_boot_config(path, 'replacement-entry')
            self.assertEqual(path.read_text(), 'original-entry')
            self.assertEqual(list(path.parent.iterdir()), [path])

    def test_missing_image_fails_before_bootloader_mutations(self):
        with tempfile.TemporaryDirectory() as directory, \
             patch.object(self.boot, 'ESP_MNT', Path(directory)), \
             patch.object(self.boot, 'get_kernels', return_value=[(Path(directory), 'linux')]), \
             patch.object(self.boot, 'ensure_esp'), patch.object(self.boot, 'run') as run:
            with self.assertRaises(RuntimeError):
                self.boot.install_systemd_boot_uefi('fixture-primary', 'fixture-fallback')
            run.assert_not_called()

    def test_bios_package_failure_cannot_report_success(self):
        def command(*argv, **kwargs):
            if argv[0] == 'pacman':
                self.assertTrue(kwargs.get('check', True))
                self.assertIn('timeout', kwargs)
                self.assertIsNone(kwargs['timeout'])
                raise subprocess.CalledProcessError(1, argv)
            return subprocess.CompletedProcess(argv, 0, 'EF02', '')
        with patch.object(self.boot, 'get_parent_disk', return_value=Path('/dev/fixture-disk')), \
             patch.object(self.boot, 'run', side_effect=command):
            with self.assertRaises(subprocess.CalledProcessError):
                self.boot.install_grub_bios_fallback({'ROOT_BLK_DEV': '/dev/fixture-root'},
                                                    {'found': False}, 'root=fixture', 'root=fixture')


if __name__ == '__main__':
    unittest.main()
