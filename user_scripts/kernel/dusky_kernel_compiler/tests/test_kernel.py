"""Isolated audit regressions: no kernel build, root, network or host writes."""
import contextlib
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tarfile
import tempfile
import tomllib
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import dusky_kernal_compile as k
import kernel_runtime as runtime


def profile():
    return k.load_profile(k.SCRIPT_DIR / 'kernel_profiles/battery.toml')


def facts(**changes):
    defaults = dict(vendor='intel', model='test', flags=frozenset(), threads=8, cores=4,
                    llc_domains=1, llc_kib=8192, mem_gib=16, swap_gib=0, disk_swap=False,
                    numa_nodes=1, virt='none', gpus=('intel',), psabi_level=3, uarch='alderlake',
                    kernel='7.3-test', cmdline='root=UUID=test rw', filesystems=('ext4',),
                    root_fs='ext4', root_luks=False, has_nvme=True, rotational=False,
                    battery=True, dkms_modules=(), bootloaders=(), esp='', xbootldr='', tools={},
                    sched_ext_live=False, initrd_compression='zstd', microcode_hook=True)
    return k.HostFacts(**(defaults | changes))


def derived(p=None, f=None, idx=None, tree=Path('.')):
    p = p or profile()
    return k.derive(p, f or facts(), idx or k.KconfigIndex(frozenset(), 4), tree, 'eevdf', False, '')


class AuditTests(unittest.TestCase):
    def test_included_profiles_preserve_zstd_and_native_thinlto(self):
        for name in ('battery', 'performance', 'extreme_power', 'low_memory'):
            with self.subTest(profile=name):
                p = k.load_profile(k.SCRIPT_DIR / 'kernel_profiles' / f'{name}.toml')
                self.assertEqual(p.g('cpu', 'arch'), 'native')
                self.assertTrue(p.g('cpu', 'compat32'))
                self.assertTrue(p.g('gaming', 'ntsync'))
                self.assertEqual(p.g('compiler', 'lto'), 'thin')
                self.assertEqual(p.g('compiler', 'optimize'), 'size' if name == 'low_memory' else 'o2')
                self.assertEqual(p.g('compiler', 'module_compress'), 'zstd')
                for key in ('zram_algo', 'zram_recomp_algo', 'zswap_compressor'):
                    self.assertEqual(p.g('memory', key), 'zstd')

    def test_matrix_single_update_preserves_config_and_escapes_strings(self):
        with tempfile.TemporaryDirectory() as directory:
            tree = Path(directory)
            cfg = tree / '.config'
            cfg.write_text('# Heading\nCONFIG_KEEP=y\nCONFIG_SWITCH=m\n'
                           '# CONFIG_SWITCH is not set\nCONFIG_TEXT="old"\n')
            mx = k.Matrix(k.KconfigIndex(frozenset(), 4))
            mx.y('SWITCH')
            value = 'path\\name "quoted" café'
            mx.s('TEXT', value)
            mx.n('OFF')
            mx.val('COUNT', 42)
            with contextlib.redirect_stdout(io.StringIO()):
                k.apply_matrix(tree, mx)
            result = cfg.read_text()
            self.assertIn('# Heading\nCONFIG_KEEP=y\n', result)
            self.assertEqual(result.count('CONFIG_SWITCH'), 1)
            self.assertEqual(k.parse_dotconfig(result),
                             {'KEEP': 'y', 'SWITCH': 'y', 'TEXT': value, 'OFF': 'n', 'COUNT': '42'})

    def test_multiline_kconfig_string_is_rejected(self):
        p = profile()
        for value in ('first\nsecond', 'first\rsecond'):
            with self.subTest(value=value):
                p.set('dusky', 'extra_config', {'TEST': value})
                with self.assertRaises(k.ProfileError):
                    k.validate_profile(p)

    def setUp(self):
        self.silence = contextlib.redirect_stdout(io.StringIO())
        self.silence.__enter__()
        k.C.disable()

    def tearDown(self):
        self.silence.__exit__(None, None, None)

    def test_profile_roundtrip(self):
        p = profile()
        raw = tomllib.loads(k.render_profile_toml(p.sections))
        sections, _ = k.coerce(raw, Path('roundtrip.toml'))
        self.assertEqual(sections, p.sections)

    def test_unknown_profile_keys_fail(self):
        for value in ({'cpu': {'typo': True}}, {'typo': {}}, {'cpu': 5}):
            with self.subTest(value=value), self.assertRaises(k.ProfileError):
                k.coerce(value, Path('test.toml'))

    def test_wrong_types_fail(self):
        for value in ({'compiler': {'jobs': 'many'}}, {'cpu': {'smt': 'flase'}}, {'modules': {'keep_symbols': [42]}}):
            with self.subTest(value=value), self.assertRaises(k.ProfileError):
                k.coerce(value, Path('test.toml'))

    def test_invalid_security_bundle_reports_profile_error(self):
        p = profile(); p.set('security', 'profile', 'typo')
        k.apply_security_bundle(p)
        with self.assertRaises(k.ProfileError): k.validate_profile(p)

    def test_rc_switch_and_pin_floor(self):
        rc = k.Release('7.3-rc4', 'mainline', '', '', None)
        self.assertEqual(k.candidates_for([rc], 'mainline', False, '7.2'), [])
        self.assertEqual(k.candidates_for([rc], 'mainline', True, '7.2'), [rc])
        p = profile(); p.set('release', 'pin', '7.2.6'); p.set('release', 'min_version', '7.3')
        with self.assertRaises(k.ProfileError): k.cross_validate(p)
        p.set('release', 'pin', '7.3-rc4'); p.set('release', 'allow_rc', False)
        with self.assertRaises(k.ProfileError): k.cross_validate(p)

    def test_included_profiles_default_to_stable_without_automatic_rc(self):
        releases = [k.Release('7.3-rc4', 'mainline', '', 'rc', None),
                    k.Release('7.3.7', 'stable', '', 'stable', None)]
        for name in ('battery', 'performance', 'extreme_power', 'low_memory'):
            with self.subTest(profile=name):
                p = k.load_profile(k.SCRIPT_DIR / 'kernel_profiles' / f'{name}.toml')
                self.assertEqual(p.g('release', 'channel'), 'stable')
                self.assertFalse(p.g('release', 'allow_rc'))
                with patch.object(k, 'interactive', return_value=True), patch.object(k, 'table'), \
                     patch.object(k, 'ask_index', side_effect=lambda _label, _maximum, default: default):
                    self.assertEqual(k.choose_release(p, releases).version, '7.3.7')
                with patch.object(k, 'interactive', return_value=False):
                    self.assertEqual(k.choose_release(p, releases).version, '7.3.7')

    def test_cli_rc_opt_in_selects_latest_without_changing_saved_profile(self):
        p = profile()
        args = k.build_parser().parse_args(['--channel', 'mainline', '--allow-rc'])
        k.apply_overrides(p, k.Overrides.from_env_and_args(args))
        k.cross_validate(p)
        releases = [k.Release('7.3-rc4', 'mainline', '', 'rc4', None),
                    k.Release('7.3-rc5', 'mainline', '', 'rc5', None)]
        with patch.object(k, 'interactive', return_value=False):
            self.assertEqual(k.choose_release(p, releases).version, '7.3-rc5')
        p.set('release', 'pin', '7.3-rc5')
        k.cross_validate(p)
        self.assertFalse(profile().g('release', 'allow_rc'))

    def test_mainline_rc_is_available_interactively_without_automatic_rc_opt_in(self):
        p = profile(); p.set('release', 'channel', 'mainline')
        releases = [k.Release('7.3-rc5', 'mainline', '2026-09-27', 'rc', None),
                    k.Release('7.2.9', 'stable', '2026-10-03', 'stable', None)]
        with patch.object(k, 'interactive', return_value=True), patch.object(k, 'warn') as warning, \
             patch.object(k, 'table') as table, \
             patch.object(k, 'ask_index', return_value=1) as choose:
            self.assertEqual(k.choose_release(p, releases).version, '7.3-rc5')
            warning.assert_not_called()
            choose.assert_called_once_with('Release', 1, 1)
            self.assertIn('profile default', table.call_args.args[1][0][4])
            self.assertIn('RC: explicit selection', table.call_args.args[1][0][4])
        self.assertFalse(p.g('release', 'allow_rc'))
        with patch.object(k, 'interactive', return_value=False):
            with self.assertRaises(k.NetworkError):
                k.choose_release(p, releases)

    def test_release_picker_shows_supported_channels_and_profile_default(self):
        releases = [k.Release('7.4-rc4', 'mainline', '2026-09-20', 'rc', None),
                    k.Release('7.3.7', 'stable', '2026-09-21', 'stable', None),
                    k.Release('6.18.53', 'longterm', '2026-09-21', 'lts', None)]
        p = profile(); p.set('release', 'channel', 'stable')
        seen = {}
        def capture_table(_headers, rows):
            seen['rows'] = rows
        def choose(_label, maximum, default):
            self.assertEqual((maximum, default), (2, 2))
            return 1
        with patch.object(k, 'interactive', return_value=True), patch.object(k, 'table', side_effect=capture_table), \
             patch.object(k, 'ask_index', side_effect=choose):
            selected = k.choose_release(p, releases)
        self.assertEqual(selected.version, '7.4-rc4')
        self.assertEqual([row[1] for row in seen['rows']], ['7.4-rc4', '7.3.7', '6.18.53'])
        self.assertIn('profile default', seen['rows'][1][4])
        self.assertEqual(seen['rows'][2][0], '–')

    def test_release_picker_respects_exact_cli_pin_and_unattended_default(self):
        releases = [k.Release('7.4-rc4', 'mainline', '', 'rc', None),
                    k.Release('7.3.7', 'stable', '', 'stable', None)]
        p = profile(); p.set('release', 'channel', 'stable'); p.set('release', 'pin', '7.4-rc4')
        with patch.object(k, 'interactive', return_value=True), patch.object(k, 'ask_index') as ask:
            self.assertEqual(k.choose_release(p, releases, exact_pin=True).version, '7.4-rc4')
            ask.assert_not_called()
        p.set('release', 'pin', '')
        with patch.object(k, 'interactive', return_value=False):
            self.assertEqual(k.choose_release(p, releases).version, '7.3.7')

    def test_future_supported_lts_is_selectable_without_code_changes(self):
        releases = [k.Release('7.4.12', 'longterm', '2027-03-01', 'future-lts', None),
                    k.Release('7.5-rc2', 'mainline', '2027-03-01', 'future-rc', None),
                    k.Release('6.18.53', 'longterm', '2026-09-21', 'old-lts', None)]
        p = profile(); p.set('release', 'channel', 'longterm')
        with patch.object(k, 'interactive', return_value=True), patch.object(k, 'table'), \
             patch.object(k, 'ask_index', side_effect=lambda _label, maximum, default: default) as ask:
            selected = k.choose_release(p, releases)
        self.assertEqual(selected.version, '7.4.12')
        self.assertEqual(ask.call_args.args[1:], (2, 2))

    def test_rc_archive_falls_back_to_tagged_source(self):
        rel = k.Release('7.3-rc4', 'mainline', '', 'https://git.kernel.org/torvalds/t/linux-7.3-rc4.tar.gz', None)
        with tempfile.TemporaryDirectory() as td, patch.object(k, 'TARBALL_DIR', Path(td)), \
             patch.object(k, 'expected_sha256', return_value=None), patch.object(k, 'download') as fetch:
            fetch.side_effect = lambda _url, dest, _fallback: dest.write_bytes(b'archive')
            self.assertEqual(k.obtain_tarball(rel, False), Path(td) / 'linux-7.3-rc4.tar.gz')
            self.assertEqual(fetch.call_args.args[2],
                             ('https://codeload.github.com/torvalds/linux/tar.gz/refs/tags/v7.3-rc4',))

    def test_download_fallback_uses_separate_partial_file(self):
        with tempfile.TemporaryDirectory() as td, patch.object(k, 'have', return_value=True):
            dest = Path(td) / 'linux-7.3-rc4.tar.gz'
            def fake_run(cmd, **_kwargs):
                if cmd[0] == 'aria2c':
                    (Path(td) / (dest.name + '.part')).write_bytes(b'incomplete')
                    return subprocess.CompletedProcess(cmd, 2)
                self.assertEqual(cmd[0], 'curl')
                self.assertNotIn('-C', cmd)
                fallback_part = Path(cmd[cmd.index('-o') + 1])
                self.assertEqual(fallback_part.name, dest.name + '.fallback1.part')
                with tarfile.open(fallback_part, 'w:gz') as archive:
                    marker = tarfile.TarInfo('linux-7.3-rc4/')
                    marker.type = tarfile.DIRTYPE
                    archive.addfile(marker)
                return subprocess.CompletedProcess(cmd, 0)
            with patch.object(k, 'run', side_effect=fake_run), patch.object(k, 'interactive', return_value=False):
                k.download('https://git.kernel.org/example', dest, ('https://codeload.github.com/example',))
            self.assertTrue(tarfile.is_tarfile(dest))
            self.assertEqual((Path(td) / (dest.name + '.part')).read_bytes(), b'incomplete')

    def test_truncated_archive_is_rejected_before_caching(self):
        with tempfile.TemporaryDirectory() as td:
            archive_path = Path(td) / 'linux.tar.gz'
            with tarfile.open(archive_path, 'w:gz') as archive:
                marker = tarfile.TarInfo('linux/')
                marker.type = tarfile.DIRTYPE
                archive.addfile(marker)
            self.assertTrue(k.archive_valid(archive_path, '.gz'))
            archive_path.write_bytes(archive_path.read_bytes()[:-8])
            self.assertFalse(k.archive_valid(archive_path, '.gz'))

    def test_interactive_download_can_retry_original_then_switch_hosts(self):
        with tempfile.TemporaryDirectory() as td, patch.object(k, 'have', return_value=True), \
             patch.object(k, 'interactive', return_value=True), patch.object(k, 'ask', side_effect=['r', 'a']) as prompt, \
             patch('time.sleep', return_value=None):
            dest = Path(td) / 'linux-7.3-rc4.tar.gz'
            attempts = []
            def fake_run(cmd, **_kwargs):
                attempts.append(cmd[0])
                if cmd[0] == 'curl':
                    with tarfile.open(Path(cmd[cmd.index('-o') + 1]), 'w:gz') as archive:
                        marker = tarfile.TarInfo('linux-7.3-rc4/')
                        marker.type = tarfile.DIRTYPE
                        archive.addfile(marker)
                    return subprocess.CompletedProcess(cmd, 0)
                return subprocess.CompletedProcess(cmd, 1)
            with patch.object(k, 'run', side_effect=fake_run):
                k.download('https://git.kernel.org/example', dest, ('https://codeload.github.com/example',))
            self.assertEqual(attempts, ['aria2c'] * 6 + ['curl'])
            self.assertEqual(prompt.call_count, 2)
            self.assertTrue(tarfile.is_tarfile(dest))

    def test_interactive_download_can_return_to_original_after_fallback(self):
        with tempfile.TemporaryDirectory() as td, patch.object(k, 'have', return_value=True), \
             patch.object(k, 'interactive', return_value=True), patch.object(k, 'ask', side_effect=['a', 'a']), \
             patch('time.sleep', return_value=None):
            dest = Path(td) / 'linux-7.3-rc4.tar.gz'
            attempts = []
            def fake_run(cmd, **_kwargs):
                attempts.append(cmd[0])
                if len(attempts) == 7:
                    self.assertEqual((Path(td) / (dest.name + '.part')).read_bytes(), b'partial')
                    with tarfile.open(Path(td) / (dest.name + '.part'), 'w:gz') as archive:
                        marker = tarfile.TarInfo('linux-7.3-rc4/')
                        marker.type = tarfile.DIRTYPE
                        archive.addfile(marker)
                    return subprocess.CompletedProcess(cmd, 0)
                if len(attempts) == 1:
                    (Path(td) / (dest.name + '.part')).write_bytes(b'partial')
                return subprocess.CompletedProcess(cmd, 1)
            with patch.object(k, 'run', side_effect=fake_run):
                k.download('https://git.kernel.org/example', dest, ('https://codeload.github.com/example',))
            self.assertEqual(attempts, ['aria2c'] * 3 + ['curl'] * 3 + ['aria2c'])
            self.assertTrue(tarfile.is_tarfile(dest))

    def test_future_cpu_names_are_not_allowlisted(self):
        p = profile(); p.set('cpu', 'arch', 'futurecpu2030')
        k.validate_profile(p)
        d = derived(p); k.build_config_matrix(p, d)
        self.assertIn('-march=futurecpu2030', d.kcflags)

    def test_cpu_native_detection_preserves_new_names(self):
        with patch.object(k, 'have', side_effect=lambda name: name == 'clang'), patch.object(k, 'run', return_value=subprocess.CompletedProcess([], 0, '"-target-cpu" "znver6"')):
            self.assertEqual(k.detect_native_uarch(), 'znver6')

    def test_manifest_missing_never_uses_host(self):
        p = profile(); p.set('meta', 'manifest_path', '/does/not/exist.json')
        with self.assertRaises(k.ProfileError): k.target_facts_for_profile(p, facts())

    def test_remote_facts_preserved(self):
        with tempfile.TemporaryDirectory() as td:
            target = facts(vendor='amd', threads=2, cores=2, numa_nodes=2, llc_domains=2,
                           swap_gib=3, disk_swap=True, gpus=(), flags=frozenset())
            manifest = Path(td)/'manifest.json'; manifest.write_text(json.dumps(target.as_json() | {'format': 'dusky_bundle_v3'}))
            p = profile(); p.set('meta', 'manifest_path', str(manifest)); p.set('meta', 'portable_package', True); p.set('cpu', 'arch', 'znver5')
            got = k.target_facts_for_profile(p, facts(flags=frozenset({'avx512f'})))
            self.assertEqual(got.as_json(), target.as_json())
            self.assertFalse(k.broad_hardware(p))
            mx = k.build_config_matrix(p, derived(p, got))
            self.assertNotIn('DRM_AMDGPU', [o.symbol for o in mx.ops])
            self.assertEqual(next(o.value for o in mx.ops if o.symbol=='NR_CPUS'), 2)

    def test_remote_pruning_never_uses_build_host(self):
        p = profile(); p.set('meta','manifest_path','target.json')
        with self.assertRaises(k.ProfileError): k.localmodconfig(Path('.'), p, None, {})

    def test_remote_native_flags_rejected(self):
        p = profile(); p.set('meta', 'portable_package', True); p.set('cpu','arch','generic')
        p.set('cpu','march','-march=native')
        with self.assertRaises(k.ProfileError): k.cross_validate(p)

    def test_single_core_jobs_and_build_host_budget(self):
        self.assertEqual(k.auto_jobs(facts(threads=1,mem_gib=1), 'thin'),1)
        with patch.object(os, 'process_cpu_count', return_value=4):
            self.assertEqual(k.auto_jobs(facts(threads=32,mem_gib=64),'thin'),4)

    def test_environment_isolation(self):
        p = profile()
        with patch.dict(os.environ, {'ARCH':'arm64', 'KBUILD_OUTPUT':'/wrong', 'HOSTCC':'wrong', 'KCFLAGS':'-bad', 'LLVM':'0'}):
            env = k.toolchain_env(p)
        self.assertEqual(env['ARCH'], 'x86'); self.assertEqual(env['LLVM'], '1')
        for key in ('KBUILD_OUTPUT','HOSTCC','KCFLAGS'): self.assertNotIn(key, env)

    def test_extra_config_wins_and_boolean_keep(self):
        p = profile(); p.set('modules','keep_symbols',['MY_BOOL','MY_DRIVER'])
        p.set('dusky','extra_config',{'MY_DRIVER':False})
        idx=k.KconfigIndex(frozenset(),4,{'MY_BOOL':'bool'})
        mx=k.build_config_matrix(p,derived(p,idx=idx))
        ops={o.symbol:o for o in mx.ops}
        self.assertEqual(ops['MY_BOOL'].action,'y'); self.assertEqual(ops['MY_DRIVER'].action,'n')

    def test_new_ntfs_driver(self):
        p=profile(); mx=k.build_config_matrix(p,derived(p))
        p.set('storage', 'extra_filesystems', ['ntfs']); mx=k.build_config_matrix(p,derived(p))
        self.assertIn('NTFS_FS',{o.symbol for o in mx.ops})
        self.assertNotIn('NTFS3_FS',{o.symbol for o in mx.ops})

    def test_stock_profiles_offer_no_patch_paths(self):
        self.assertEqual(k.SCHED_CHOICES, ('eevdf',))
        self.assertEqual(k.HZ_CHOICES, (100, 250, 300, 1000))
        for name in ('battery', 'performance', 'extreme_power', 'low_memory'):
            p = k.load_profile(k.SCRIPT_DIR / 'kernel_profiles' / f'{name}.toml')
            self.assertEqual(p.g('modules', 'keep_symbols'), [])
            self.assertEqual(p.g('dusky', 'extra_config'), {})
        with self.assertRaises(k.ProfileError):
            k.coerce({'dusky': {'patch_sched_inline': True}}, Path('old.toml'))

    def test_scheduler_requires_tracing(self):
        p=profile();p.set('scheduler','scx','scx_lavd');p.set('memory','tracing','minimal')
        k.normalize_profile(p)
        self.assertEqual(p.g('memory','tracing'),'full')

    def test_must_be_builtin_not_module(self):
        p=profile();p.set('verify','strict',False)
        p.set('verify','require_ntsync',False);p.set('verify','require_btf',False);p.set('verify','require_sched_ext',False)
        with tempfile.TemporaryDirectory() as td:
            tree=Path(td);(tree/'.config').write_text('CONFIG_ROOT_FS=m\nCONFIG_HZ=300\nCONFIG_LOCALVERSION="-dusky-battery"\n')
            mx=k.Matrix(k.KconfigIndex(frozenset({'ROOT_FS'}),4));mx.y('ROOT_FS')
            r=k.verify_config(tree,p,mx,derived(p,tree=tree))
            self.assertIn('ROOT_FS',[o.symbol for o,_ in r.hard])

    def test_explicit_missing_symbol_fails(self):
        p=profile();p.set('verify','strict',False);p.set('dusky','extra_config',{'MISSING':True})
        with tempfile.TemporaryDirectory() as td:
            tree=Path(td);(tree/'.config').write_text('CONFIG_HZ=300\n')
            mx=k.Matrix(k.KconfigIndex(frozenset({'OTHER'}),4));mx.y('MISSING')
            r=k.verify_config(tree,p,mx,derived(p,tree=tree))
            self.assertIn('MISSING',[o.symbol for o,_ in r.hard])

    def test_runtime_zero_is_zero(self):
        p=profile();p.set('memory','compaction_proactiveness',0)
        self.assertEqual(runtime.tuning_values(p.sections)['proc/sys/vm/compaction_proactiveness'],0)

    def test_runtime_matches_kernel_before_writing(self):
        with tempfile.TemporaryDirectory() as td:
            path=Path(td)/'profile.json';path.write_text(json.dumps({'kernelrelease':'not-running','profile':profile().sections}))
            with patch.object(sys,'argv',['runtime',str(path)]), patch.object(runtime,'apply') as apply, patch.object(runtime,'setup_zram') as zram:
                self.assertEqual(runtime.main(),0);apply.assert_not_called();zram.assert_not_called()

    def test_runtime_uses_mock_filesystem(self):
        p=profile()
        with tempfile.TemporaryDirectory() as td:
            root=Path(td)
            for name in runtime.tuning_values(p.sections):
                path=root/name;path.parent.mkdir(parents=True,exist_ok=True);path.write_text('')
            policy=root/'sys/devices/system/cpu/cpufreq/policy0';policy.mkdir(parents=True)
            (policy/'scaling_available_governors').write_text('powersave performance')
            (policy/'energy_performance_preference').write_text('default')
            self.assertEqual(runtime.apply(p.sections,root),0)
            self.assertEqual((policy/'scaling_governor').read_text().strip(),'powersave')
            self.assertEqual((policy/'energy_performance_preference').read_text().strip(),'power')

    def test_zram_opt_out(self):
        p=profile();p.set('runtime','manage_zram',False)
        with patch.object(runtime.subprocess,'run') as run:
            runtime.setup_zram(p.sections);run.assert_not_called()

    def test_package_runtime_integration(self):
        with tempfile.TemporaryDirectory() as td:
            tree=Path(td);path=tree/'scripts/package/PKGBUILD';path.parent.mkdir(parents=True)
            path.write_text('pkgname=(linux-dusky-battery)\n_package() { :; }\nfor _p in "${pkgname[@]}"; do\n eval "package_$_p() {\n $(declare -f _package)\n _package\n }"\ndone\n')
            p=profile();d=derived(p,tree=tree);d.kernelrelease='7.3-dusky-battery'
            k.stage_runtime_package(tree,p,d);first=path.read_text();k.stage_runtime_package(tree,p,d)
            self.assertEqual(first,path.read_text())
            root=tree/'pkg'
            subprocess.run(['bash','-eu','-c','source "$1"; package_linux-dusky-battery','test',str(path)],env=os.environ|{'srctree':str(tree),'pkgdir':str(root)},check=True)
            self.assertTrue((root/'usr/lib/dusky-kernel/linux-dusky-battery/profile.json').is_file())
            unit=root/'usr/lib/systemd/system/multi-user.target.wants/linux-dusky-battery-tuning.service'
            self.assertTrue(unit.is_symlink());self.assertTrue(unit.exists())

    def test_runtime_opt_out_keeps_metadata_but_no_service(self):
        p=profile();p.set('runtime','enabled',False)
        with tempfile.TemporaryDirectory() as td:
            tree=Path(td);pkg=tree/'scripts/package/PKGBUILD';pkg.parent.mkdir(parents=True)
            pkg.write_text('_package() { :; }\nfor _p in "${pkgname[@]}"; do :; done\n')
            k.stage_runtime_package(tree,p,derived(p))
            self.assertTrue((tree/'.dusky/runtime/profile.json').exists())
            self.assertNotIn('multi-user.target.wants',pkg.read_text())
            self.assertNotIn('depends+=(python',pkg.read_text())

    def test_localyesconfig_applies_after_overrides(self):
        p=profile();p.set('modules','localyesconfig',True)
        self.assertFalse(any(op.action=='m' for op in k.build_config_matrix(p,derived(p)).ops))

    def test_extmod_flags_and_toolchain_are_idempotent(self):
        p=profile();p.set('cpu','arch','core2');d=derived(p);k.build_config_matrix(p,d)
        env=k.build_env(p,d,facts(),0)
        with tempfile.TemporaryDirectory() as td:
            tree=Path(td);mf=tree/'Makefile';mf.write_text('export KBUILD_EXTMOD\n')
            k.prepare_extmod_build(tree,d,env);first=mf.read_text();k.prepare_extmod_build(tree,d,env)
            self.assertEqual(first,mf.read_text());self.assertIn('LLVM ?= 1',first)
            self.assertIn('override LD := ld.lld',first)
            self.assertIn('-march=core2',first);self.assertNotIn('KBUILD_CPPFLAGS',first)

    def test_thinlto_policy_is_preflighted_before_makefile_injection(self):
        with tempfile.TemporaryDirectory() as td:
            tree = Path(td)
            (tree / 'Makefile').write_text('VERSION = 7\nPATCHLEVEL = 3\n')
            p = profile(); d = derived(p, tree=tree); d.lto = 'thin'
            commands = []
            def fake_run(cmd, **kwargs):
                commands.append(cmd)
                if cmd[0] == 'ld.lld':
                    (kwargs['cwd'] / 'probe.so').write_bytes(b'linked')
                return subprocess.CompletedProcess(cmd, 0, '')
            with patch.object(k, 'THINLTO_CACHE_DIR', tree / 'persistent-cache'), \
                 patch.object(k, 'host_facts', return_value=facts()), patch.object(k, 'run', side_effect=fake_run):
                k.link_thinlto_cache(tree, p, d)
            self.assertEqual([cmd[0] for cmd in commands], ['clang', 'ld.lld'])
            policy = '--thinlto-cache-policy=cache_size=0%:cache_size_bytes=20g'
            self.assertIn(policy, commands[1])
            self.assertIn(policy, (tree / 'Makefile').read_text())

    def test_zero_job_override_restores_auto(self):
        p=profile();p.set('compiler','jobs',8)
        k.apply_overrides(p,k.Overrides(jobs=0))
        self.assertEqual(p.g('compiler','jobs'),0)

    def test_schema_fields_unique(self):
        for sec,fields in k.PROFILE_SPEC.items():
            self.assertEqual(len(fields),len({f.key for f in fields}),sec)

    def test_remote_profile_import_uses_selected_tuning(self):
        import tarfile
        with tempfile.TemporaryDirectory() as td:
            root=Path(td);manifest=root/'manifest.json'
            manifest.write_text(json.dumps(facts(threads=2,psabi_level=1,uarch='').as_json() | {'format':'dusky_bundle_v3','hostname':'oldpc'}))
            db=root/'modprobed.db';db.write_text('ext4\nahci\n')
            bundle=root/'bundle.tar.gz'
            with tarfile.open(bundle,'w:gz') as tf:
                tf.add(manifest,arcname='manifest.json');tf.add(db,arcname='modprobed.db')
            profiles=root/'profiles';profiles.mkdir()
            with patch.object(k,'IMPORT_DIR',root/'imports'),patch.object(k,'PROFILES_DIR',profiles),patch.object(k,'ensure_profiles_exist',return_value=[profile()]),patch.object(k,'host_facts',return_value=facts()):
                name=k.do_import_bundle(bundle,'battery')
            got=k.load_profile(profiles/(name+'.toml'))
            self.assertEqual(got.g('cpu','arch'),'generic')
            self.assertEqual(got.g('timing','hz'),profile().g('timing','hz'))
            self.assertEqual(got.g('cpu','epp'),profile().g('cpu','epp'))
            self.assertEqual(got.g('scheduler','scx'),'none')

    def test_remote_native_guard_does_not_depend_on_portable_flag(self):
        p = profile(); p.set('meta', 'manifest_path', 'target.json')
        p.set('meta', 'portable_package', False)
        with self.assertRaises(k.ProfileError): k.cross_validate(p)
        p.set('cpu', 'arch', 'core2')
        p.set('dusky', 'extra_config', {'CONFIG_X86_NATIVE_CPU': True})
        with self.assertRaises(k.ProfileError): k.cross_validate(p)

    def test_ram_job_budget_uses_available_memory_and_reserve(self):
        with patch.object(k, '_read', return_value='MemAvailable: 16777216 kB'), patch.object(k, 'RAM_RESERVE_GIB', 8), patch.object(os, 'process_cpu_count', return_value=32):
            self.assertEqual(k.auto_jobs(facts(threads=32, mem_gib=64), 'thin'), 6)

    def test_ram_session_routes_selected_tree_and_restores_paths(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td); disk = root / 'disk'; disk.mkdir()
            cfg = dict(persistent_dir=disk, packages_dir=disk / 'packages',
                       thinlto_dir=disk / 'thinlto-cache', ccache_dir=disk / 'ccache',
                       zram_dir=root / 'ram', ram_reserve_gib=8)
            for name in ('linux-selected', 'linux-other'):
                obj = disk / 'src' / name / 'test.o'
                obj.parent.mkdir(parents=True); obj.write_bytes(name.encode())
            values = dict(STORAGE=cfg, BUILD_DIR=disk, SRC_DIR=disk / 'src',
                          THINLTO_CACHE_DIR=cfg['thinlto_dir'], CCACHE_DIR=cfg['ccache_dir'],
                          TARBALL_DIR=disk / 'tarballs',
                          PKGDEST_DIR=cfg['packages_dir'], IMPORT_DIR=root / 'imports', RAM_RESERVE_GIB=0)
            def rsync(argv):
                return subprocess.run(argv, check=True, capture_output=True)
            with patch.multiple(k, **values), patch.object(k, 'run', side_effect=rsync), \
                 patch.object(k.kernel_storage, 'ram_mount', side_effect=lambda p: root / 'ram' in p.parents):
                with self.assertRaises(KeyboardInterrupt):
                    with k.storage_session(True, 'linux-selected'):
                        self.assertNotEqual(k.BUILD_DIR, disk)
                        self.assertEqual([p.name for p in k.SRC_DIR.iterdir()], ['linux-selected'])
                        self.assertEqual(k.CCACHE_DIR, k.BUILD_DIR / 'ccache')
                        self.assertEqual(k.THINLTO_CACHE_DIR, k.BUILD_DIR / 'thinlto-cache')
                        self.assertEqual(k.RAM_RESERVE_GIB, 8)
                        (k.SRC_DIR / 'linux-selected/test.o').write_bytes(b'checkpointed')
                        raise KeyboardInterrupt()
                self.assertEqual(k.BUILD_DIR, disk)
                self.assertEqual(k.CCACHE_DIR, cfg['ccache_dir'])
                self.assertEqual(k.THINLTO_CACHE_DIR, cfg['thinlto_dir'])
                self.assertEqual(k.RAM_RESERVE_GIB, 0)
            self.assertEqual((disk / 'src/linux-selected/test.o').read_bytes(), b'checkpointed')
            self.assertEqual((disk / 'src/linux-other/test.o').read_bytes(), b'linux-other')

    def test_ram_choice_uses_capacity_without_prompt(self):
        args = k.build_parser().parse_args([])
        for ready in (True, False):
            with patch.object(k.kernel_storage, 'ram_capacity', return_value=(ready, 'measured')), patch('builtins.input', side_effect=AssertionError('no prompt')):
                self.assertEqual(k.choose_ram_build(args), ready)

    def test_ram_choice_prompts_and_aborts_on_high_ram_fallback(self):
        args = k.build_parser().parse_args([])
        gib = 1 << 30
        res = k.kernel_storage.RamCapacity(False, 'low available memory', phys_ram_sufficient=True,
                                           total_ram=64 * gib, needed=22 * gib, budget=12 * gib,
                                           available=20 * gib, reserve=8 * gib)
        with patch.object(k.kernel_storage, 'ram_capacity', return_value=res):
            with patch('builtins.input', return_value='n'), patch.object(k, 'interactive', return_value=True):
                with self.assertRaises(k.AbortError):
                    k.choose_ram_build(args)

    def test_show_ram_fallback_warning_renders_rich_panel(self):
        gib = 1 << 30
        res = k.kernel_storage.RamCapacity(False, 'low memory', phys_ram_sufficient=True,
                                           total_ram=64 * gib, needed=30 * gib, budget=12 * gib,
                                           available=20 * gib, reserve=8 * gib)
        with patch.object(k, 'say') as mock_say:
            k.show_ram_fallback_warning(res, lto='thin')
            self.assertTrue(mock_say.called)
            # Ensure lines were emitted
            captured = "".join(call[0][0] for call in mock_say.call_args_list)
            self.assertIn("RAM Build Fallback", captured)
            self.assertIn("64.0 GiB", captured)

    def test_ram_choice_proceeds_on_ssd_when_confirmed_or_no_prompt(self):
        args = k.build_parser().parse_args([])
        gib = 1 << 30
        res = k.kernel_storage.RamCapacity(False, 'low available memory', phys_ram_sufficient=True,
                                           total_ram=64 * gib, needed=22 * gib, budget=12 * gib)
        with patch.object(k.kernel_storage, 'ram_capacity', return_value=res):
            with patch('builtins.input', return_value='y'), patch.object(k, 'interactive', return_value=True):
                self.assertFalse(k.choose_ram_build(args))
            no_prompt_args = k.build_parser().parse_args(['--no-prompt'])
            with patch('builtins.input', side_effect=AssertionError('no prompt')):
                self.assertFalse(k.choose_ram_build(no_prompt_args))

    def test_checkpoint_runs_after_abort(self):
        k._ABORT.set()
        try:
            def checkpoint(cmd):
                self.assertFalse(k._ABORT.is_set())
            with patch.object(k, 'run', side_effect=checkpoint):
                k.checkpoint_run(['rsync'])
            self.assertTrue(k._ABORT.is_set())
        finally:
            k._ABORT.clear()

    def test_remote_bundle_preserves_exported_profile(self):
        import tarfile
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            target = facts(threads=2, uarch='core2', psabi_level=1)
            db = root / 'modprobed.db'; db.write_text('ext4\nahci\n')
            bundle = root / 'bundle.tar.zst'
            custom = profile(); custom.set('timing', 'hz', 1000)
            with patch.object(k, 'host_facts', return_value=target), patch.object(k, 'have', return_value=False), patch.object(k, 'resolve_modprobed_db', return_value=db), patch.object(k, 'ensure_profiles_exist', return_value=[custom]):
                k.do_export_bundle(bundle, 'battery')
            self.assertEqual(bundle.read_bytes()[:4], bytes.fromhex('28b52ffd'))
            with tarfile.open(bundle) as tf:
                self.assertIn('profile.toml', tf.getnames())
            profiles = root / 'profiles'; profiles.mkdir()
            with patch.object(k, 'IMPORT_DIR', root / 'imports'), patch.object(k, 'PROFILES_DIR', profiles), patch.object(k, 'host_facts', return_value=facts()), patch.object(k, 'select_profile', side_effect=AssertionError('must use bundled profile')):
                name = k.do_import_bundle(bundle)
            imported = k.load_profile(profiles / (name + '.toml'))
            self.assertEqual(imported.g('timing', 'hz'), 1000)
            self.assertEqual(imported.g('cpu', 'arch'), 'core2')
            self.assertEqual(imported.g('cpu', 'march'), '')
            remote = k.target_facts_for_profile(imported, facts())
            self.assertEqual(remote.threads, 2)

    def test_added_profiles_are_strict_and_machine_neutral(self):
        for name in ('performance', 'extreme_power', 'low_memory'):
            p = k.load_profile(k.SCRIPT_DIR / 'kernel_profiles' / (name + '.toml'))
            k.normalize_profile(p); k.cross_validate(p)
            self.assertEqual(p.g('modules', 'mode'), 'strict')
            self.assertFalse(p.g('modules', 'allow_lsmod_fallback'))
            self.assertEqual(p.g('cpu', 'arch'), 'native')
            self.assertEqual(p.g('boot', 'cmdline_extra'), '')
            self.assertEqual(p.g('modules', 'keep_symbols'), [])
            self.assertEqual(p.g('dusky', 'extra_config'), {})

    def test_sparse_possible_cpu_ids_size_all_slots(self):
        with patch.object(k, '_read', return_value='0-3,8-11'):
            self.assertEqual(k.possible_cpu_count(), 12)

    def test_custom_cpu_overrides_match_c_and_rust(self):
        p = profile(); p.set('cpu', 'march', '-mtune=znver5 -march=znver4')
        d = derived(p); d.rust = True
        k._ops_uarch(k.Matrix(d.idx), p, d)
        self.assertEqual(d.march, 'znver4'); self.assertEqual(d.mtune, 'znver5')
        self.assertIn('-march=znver4', d.kcflags)
        self.assertIn('-Ctarget-cpu=znver4', d.krustflags)
        self.assertIn('-Ztune-cpu=znver5', d.krustflags)

    def test_rust_unknown_cpu_cannot_silently_fall_back(self):
        d = derived(); d.rust = True; d.march = 'futurecpu'; d.mtune = 'futurecpu'
        with patch.object(k, 'run', return_value=subprocess.CompletedProcess([], 0, 'x86-64\nznver5')):
            with self.assertRaises(k.ProfileError): k.validate_rust_cpu(d)

    def test_strict_intel_gpu_retains_only_census_driver(self):
        with tempfile.TemporaryDirectory() as td:
            tree = Path(td); (tree / '.config').write_text('CONFIG_DRM_I915=m\n# CONFIG_DRM_XE is not set\n')
            idx = k.KconfigIndex(frozenset({'DRM_I915', 'DRM_XE', 'PREEMPT_LAZY'}), 4)
            p = profile(); d = derived(p, idx=idx, tree=tree); mx = k.Matrix(idx)
            k._ops_gpu(mx, p, d)
            self.assertIn('DRM_I915', [o.symbol for o in mx.ops])
            self.assertNotIn('DRM_XE', [o.symbol for o in mx.ops])
            (tree / '.config').write_text('# CONFIG_DRM_I915 is not set\n')
            with self.assertRaises(k.ProfileError): k._ops_gpu(k.Matrix(idx), p, d)

    def test_nvidia_dkms_never_adds_rc_nouveau_fallback(self):
        idx = k.KconfigIndex(frozenset({'DRM_NOUVEAU', 'PREEMPT_LAZY'}), 4)
        for version in ('7.3', '7.3-rc7', '7.4-rc1'):
            for vendors in (('nvidia',), ('intel', 'nvidia'), ('amd', 'nvidia')):
                for dkms in ((), ('nvidia',), ('nvidia-open-dkms',)):
                    with self.subTest(version=version, vendors=vendors, dkms=dkms), patch.object(k, '_gpu_hide_active', return_value=False):
                        p = profile(); d = derived(p, facts(gpus=vendors, dkms_modules=dkms), idx)
                        d.version = version
                        # No .config exists here: Intel census validation has a separate test.
                        with tempfile.TemporaryDirectory() as td:
                            d.tree = Path(td)
                            mx = k.Matrix(idx); k._ops_gpu(mx, p, d)
                        ops = {o.symbol:o.action for o in mx.ops}
                        self.assertEqual(ops['DRM_NOUVEAU'], 'n' if dkms else 'm')

    def test_portable_kernel_keeps_nouveau_independent_of_builder_dkms(self):
        p = profile(); p.set('meta', 'portable_package', True)
        d = derived(p, facts(gpus=('nvidia',), dkms_modules=('nvidia',)))
        d.version = '7.3-rc7'
        mx = k.Matrix(d.idx); k._ops_gpu(mx, p, d)
        self.assertEqual({o.symbol:o.action for o in mx.ops}['DRM_NOUVEAU'], 'm')

    def test_network_only_enables_selected_algorithms(self):
        p = profile(); idx = k.KconfigIndex(frozenset({'PREEMPT_LAZY', 'TCP_CONG_BBR', 'TCP_CONG_CUBIC', 'NET_SCH_CAKE', 'NET_SCH_FQ', 'NET_SCH_FQ_CODEL'}), 4)
        mx = k.Matrix(idx); k._ops_network(mx, p, derived(p, idx=idx))
        ops = {o.symbol:o.action for o in mx.ops}
        self.assertEqual(ops['TCP_CONG_CUBIC'], 'n'); self.assertEqual(ops['NET_SCH_CAKE'], 'n')
        self.assertEqual(ops['TCP_CONG_BBR'], 'y'); self.assertEqual(ops['NET_SCH_FQ'], 'y')

    def test_recorded_virtio_mem_retains_hotplug_on_lean_bare_metal(self):
        with tempfile.TemporaryDirectory() as td:
            tree = Path(td); (tree / '.config').write_text('CONFIG_VIRTIO_MEM=m\n')
            p = profile(); mx = k.Matrix(k.KconfigIndex(frozenset(), 4))
            k._ops_memory(mx, p, derived(p, tree=tree))
            ops = {o.symbol: o.action for o in mx.ops}
            self.assertEqual(ops['VIRTIO_MEM'], 'm')
            self.assertEqual(ops['MEMORY_HOTPLUG'], 'y')
            self.assertEqual(ops['MEMORY_HOTREMOVE'], 'y')

    def test_network_preserves_recorded_cake_without_changing_default(self):
        with tempfile.TemporaryDirectory() as td:
            tree = Path(td)
            (tree / '.config').write_text('CONFIG_NET_SCH_CAKE=m\n')
            p = profile(); mx = k.Matrix(k.KconfigIndex(frozenset(), 4))
            k._ops_network(mx, p, derived(p, tree=tree))
            ops = {o.symbol: o.action for o in mx.ops}
            self.assertEqual(ops['NET_SCH_CAKE'], 'm')
            self.assertEqual(ops['DEFAULT_FQ'], 'y')

    def test_arch_seed_uses_complete_installed_config_when_fetch_fails(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td); module = root / 'linux-test'
            cfg = module / 'build/.config'; cfg.parent.mkdir(parents=True)
            (module / 'pkgbase').write_text('linux\n')
            cfg.write_text('CONFIG_X86_64=y\nCONFIG_MODULES=y\n' + '# padding\n' * 2500)
            original = Path.iterdir
            def entries(path):
                return iter([module]) if path == Path('/usr/lib/modules') else original(path)
            with patch.object(k, 'BUILD_DIR', root), patch.object(Path, 'iterdir', entries), \
                 patch.object(k, 'http_get', side_effect=k.NetworkError('offline')):
                self.assertEqual(k.arch_upstream_config(), cfg)

    def test_stream_callback_failure_terminates_child(self):
        processes = []
        popen = subprocess.Popen
        def child(*args, **kwargs):
            proc = popen(*args, **kwargs); processes.append(proc); return proc
        def fail(_line):
            raise ValueError('monitor failed')
        with patch.object(k.subprocess, 'Popen', side_effect=child):
            with self.assertRaises(ValueError):
                k.run_stream([sys.executable, '-u', '-c', 'import time; print("ready"); time.sleep(60)'], on_line=fail)
        self.assertIsNotNone(processes[0].poll())
        self.assertNotIn(processes[0].pid, k._CHILD_PGIDS)

    def test_active_pstate_maps_dynamic_governor(self):
        p = profile(); p.set('cpu', 'governor', 'schedutil')
        with tempfile.TemporaryDirectory() as td:
            root = Path(td); policy = root / 'sys/devices/system/cpu/cpufreq/policy0'; policy.mkdir(parents=True)
            (policy / 'scaling_available_governors').write_text('performance powersave')
            (policy / 'scaling_governor').write_text('performance')
            with patch.object(runtime, 'tuning_values', return_value={}):
                self.assertEqual(runtime.apply(p.sections, root), 0)
            self.assertEqual((policy / 'scaling_governor').read_text().strip(), 'powersave')

    def test_dkms_autoinstall_covers_missing_target_entries(self):
        old = subprocess.CompletedProcess([], 0, 'nvidia/1.0, 7.2-old, x86_64: installed\n')
        new = subprocess.CompletedProcess([], 0, 'nvidia/1.0, 7.3-test, x86_64: installed\n')
        done = subprocess.CompletedProcess([], 0, '')
        with patch.object(k, 'have', return_value=True), patch.object(k, '_kernelreleases_for_pkgbases', return_value={'7.2-old':'linux-test', '7.3-test':'linux-test'}), patch.object(k.PRIV, 'ensure'), patch.object(k.PRIV, 'run', return_value=done) as privileged, patch.object(k, 'run', side_effect=[old, new, new]):
            self.assertTrue(k.audit_dkms('7.3-test', 'linux-test'))
            privileged.assert_called_once_with(['dkms', 'autoinstall', '-k', '7.3-test'], check=False)

    def test_dkms_status_failure_is_not_success(self):
        with patch.object(k, 'have', return_value=True), patch.object(k, '_kernelreleases_for_pkgbases', return_value={'7.3-test':'linux-test'}), patch.object(k.PRIV, 'ensure'), patch.object(k.PRIV, 'run', return_value=subprocess.CompletedProcess([], 0, '')), patch.object(k, 'run', return_value=subprocess.CompletedProcess([], 1, 'broken status')):
            self.assertFalse(k.audit_dkms('7.3-test', 'linux-test'))

    def test_dkms_ignores_old_kernel_with_same_pkgbase(self):
        statuses = subprocess.CompletedProcess([], 0, 'nvidia/1.0, 7.2-old, x86_64: built\nnvidia/1.0, 7.3-test, x86_64: installed\n')
        with patch.object(k, 'have', return_value=True), patch.object(k, '_kernelreleases_for_pkgbases', return_value={'7.2-old':'linux-test', '7.3-test':'linux-test'}), patch.object(k.PRIV, 'run') as privileged, patch.object(k, 'run', return_value=statuses):
            self.assertTrue(k.audit_dkms('7.3-test', 'linux-test'))
            privileged.assert_not_called()

    def test_dkms_missing_new_kernel_module_fails_even_if_old_is_installed(self):
        statuses = subprocess.CompletedProcess([], 0, 'nvidia/1.0, 7.2-old, x86_64: installed\n')
        failed = subprocess.CompletedProcess([], 10, '')
        with patch.object(k, 'have', return_value=True), patch.object(k, '_kernelreleases_for_pkgbases', return_value={'7.2-old':'linux-test', '7.3-test':'linux-test'}), patch.object(k.PRIV, 'ensure'), patch.object(k.PRIV, 'run', return_value=failed), patch.object(k, 'run', return_value=statuses):
            self.assertFalse(k.audit_dkms('7.3-test', 'linux-test'))

    def test_saved_package_identifies_exact_kernelrelease(self):
        with tempfile.TemporaryDirectory() as td:
            pkg = Path(td) / 'linux-test.pkg.tar.gz'
            with tarfile.open(pkg, 'w:gz') as archive:
                content = b'linux-test\n'
                member = tarfile.TarInfo('./usr/lib/modules/7.3-test/pkgbase')
                member.size = len(content)
                archive.addfile(member, io.BytesIO(content))
            self.assertEqual(k.packaged_kernelrelease([pkg], 'linux-test'), '7.3-test')

    def test_manifest_rejects_corrupt_cpu_count(self):
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / 'manifest.json'
            path.write_text(json.dumps(facts().as_json() | {'format':'dusky_bundle_v3','threads':'64'}))
            p = profile(); p.set('meta', 'manifest_path', str(path))
            with self.assertRaisesRegex(k.ProfileError, 'invalid target threads'):
                k.target_facts_for_profile(p, facts())

    def test_census_repairs_generated_intel_drivers_and_respects_override(self):
        with tempfile.TemporaryDirectory() as td:
            tree = Path(td)
            (tree / '.config').write_text('CONFIG_INTEL_HID_EVENT=m\nCONFIG_DRM_I915=m\n')
            makefile = tree / 'drivers/platform/x86/intel/Makefile'
            makefile.parent.mkdir(parents=True)
            makefile.write_text('intel-target-$(CONFIG_INTEL_HID_EVENT) += hid.o\nintel-target-$(CONFIG_INTEL_VSEC) += vsec.o\n')
            p = profile()
            result = subprocess.CompletedProcess([], 0, 'intel_hid config not found!\nacpi_call config not found!\n')
            with patch.object(k, 'run', return_value=result):
                restored = k.localmodconfig(tree, p, tree / 'db', {})
            self.assertEqual(restored, {'INTEL_HID_EVENT'})
            d = derived(p, tree=tree); d.census_symbols = restored
            ops = {o.symbol: o.action for o in k.build_config_matrix(p, d).ops}
            self.assertEqual(ops['INTEL_HID_EVENT'], 'm')
            p.set('dusky', 'extra_config', {'INTEL_HID_EVENT': False})
            ops = {o.symbol: o.action for o in k.build_config_matrix(p, d).ops}
            self.assertEqual(ops['INTEL_HID_EVENT'], 'n')

    def test_never_thp_policy_keeps_gpu_support_and_prunes_unused_zram_backends(self):
        p = profile(); p.set('memory', 'zram_algo', 'zstd')
        ops = {o.symbol: o.action for o in k.build_config_matrix(p, derived(p)).ops}
        self.assertEqual(ops['TRANSPARENT_HUGEPAGE'], 'y')
        self.assertEqual(ops['TRANSPARENT_HUGEPAGE_NEVER'], 'y')
        self.assertEqual(ops['ZRAM_BACKEND_LZ4'], 'n')
        self.assertEqual(ops['ZRAM_BACKEND_ZSTD'], 'y')

    def test_core_matrix_includes_binder_and_veth_for_containers(self):
        p = profile()
        ops_dict = {o.symbol: o for o in k.build_config_matrix(p, derived(p)).ops}
        self.assertEqual(ops_dict['ANDROID_BINDER_IPC'].action, 'y')
        self.assertEqual(ops_dict['ANDROID_BINDERFS'].action, 'y')
        self.assertEqual(ops_dict['ANDROID_BINDER_DEVICES'].value, 'binder,hwbinder,vndbinder')
        self.assertEqual(ops_dict['VETH'].action, 'm')
        self.assertEqual(ops_dict['BRIDGE'].action, 'm')
        self.assertEqual(ops_dict['IP_NF_MANGLE'].action, 'm')
        self.assertEqual(ops_dict['NETFILTER_XT_TARGET_CHECKSUM'].action, 'm')
        self.assertEqual(ops_dict['MEMFD_CREATE'].action, 'y')

    def test_single_numa_node_does_not_compile_numa(self):
        p = profile(); p.set('memory', 'numa', True)
        for nodes, expected in ((1, 'n'), (2, 'y')):
            ops = {o.symbol: o.action for o in k.build_config_matrix(p, derived(p, facts(numa_nodes=nodes))).ops}
            self.assertEqual(ops['NUMA'], expected)

    def test_vm_manifest_with_no_l3_is_valid(self):
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / 'manifest.json'
            path.write_text(json.dumps(facts(virt='kvm', llc_domains=0, llc_kib=0).as_json() | {'format': 'dusky_bundle_v3'}))
            p = profile(); p.set('meta', 'manifest_path', str(path))
            self.assertEqual(k.target_facts_for_profile(p, facts()).llc_domains, 0)

    def test_fdo_first_pass_keeps_line_information_without_profile(self):
        with tempfile.TemporaryDirectory() as td:
            p = profile(); p.set('compiler', 'fdo', 'autofdo'); p.set('compiler', 'fdo_profile_dir', td)
            k.normalize_profile(p)
            self.assertEqual(p.g('compiler', 'debug_info'), 'full')
            d = derived(p)
            self.assertEqual(d.fdo, 'autofdo')
            env = k.build_env(p, d, facts(), 0)
            self.assertNotIn('CLANG_AUTOFDO_PROFILE', env)

    def test_timeout_without_process_group_reaps_child(self):
        processes = []; popen = subprocess.Popen
        def child(*args, **kwargs):
            proc = popen(*args, **kwargs); processes.append(proc); return proc
        with patch.object(k.subprocess, 'Popen', side_effect=child):
            with self.assertRaises(k.BuildError):
                k.run([sys.executable, '-c', 'import time; time.sleep(60)'], timeout=0.05, own_group=False)
        self.assertIsNotNone(processes[0].poll())

    def test_gpg_signing_subkey_uses_primary_fingerprint(self):
        import gzip
        primary = next(iter(k.KERNEL_SIGNING_FPRS))
        for signer, expected in ((primary, True), ('A'*40, False)):
            with tempfile.TemporaryDirectory() as td:
                archive = Path(td) / 'linux.tar.gz'
                archive.write_bytes(gzip.compress(b'kernel source'))
                (Path(td) / 'linux.tar.sign').write_bytes(b'test signature')
                popen = subprocess.Popen
                def process(cmd, **kwargs):
                    if cmd[0] == 'gpg':
                        status = f'[GNUPG:] VALIDSIG {"B"*40} 2026-10-04 1 0 4 0 1 10 00 {signer}'
                        cmd = [sys.executable, '-c', f'import sys; sys.stdin.buffer.read(); print({status!r})']
                    return popen(cmd, **kwargs)
                with patch.object(k, 'ensure_kernel_keys', return_value=True), patch.object(k.subprocess, 'Popen', side_effect=process):
                    self.assertEqual(k.verify_pgp(archive, 'https://example/linux.tar.sign'), expected)
                self.assertEqual(k._CHILD_PGIDS, set())

    def test_wizard_schema_references(self):
        for step in k.WIZARD_STEPS:
            for sec,keys in step.groups:
                self.assertTrue(set(keys) <= {f.key for f in k.PROFILE_SPEC[sec]},(sec,keys))


if __name__=='__main__': unittest.main()
