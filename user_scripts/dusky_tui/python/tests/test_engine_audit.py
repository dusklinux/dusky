"""Isolated Python 3.15 engine regressions; never writes live system settings."""
from concurrent.futures import ThreadPoolExecutor
import json
import math
import os
from pathlib import Path
import random
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
import python.engines
if original := os.environ.get('DUSKY_AUDIT_ORIGINALS'):
    python.engines.__path__ = [original, *python.engines.__path__]
from python.engines.autostart_engine import AutostartLuaEngine, AUTOSTART_DEFAULTS
from python.engines.cmdline import CmdlineEngine
from python.engines.environment_variables import ShellEnvEngine
from python.engines.json_engine import JsonEngine
from python.engines.monitor_engine import MonitorLuaEngine, clean_scale, is_sharp, format_scale, grid_scales
from python.engines.systemd_boot import SystemdBootEngine

FIXTURES = ((CmdlineEngine, 'x=1\n', 'x', 'DEFAULT/x'),
            (ShellEnvEngine, 'X=1\n', 'X', 'DEFAULT/X'),
            (JsonEngine, '{"x":1}', 'x', 'x'),
            (SystemdBootEngine, 'title Fixture\noptions x=1\n', 'x', 'DEFAULT/x'))

class EngineAuditTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix='dusky-audit-')
        self.addCleanup(temporary.cleanup)
        self.home = Path(temporary.name)
        self.path = self.home / 'config.lua'
        self.loader = self.home / 'loader.conf'
        self.loader.write_text('timeout 3\n', encoding='utf-8')
        for context in (
            patch.dict(os.environ, {'HOME': str(self.home), 'XDG_CONFIG_HOME': str(self.home / '.config')}),
            patch('python.engines.systemd_boot.discover_all_kernels_and_entries', return_value={}),
            patch.object(SystemdBootEngine, '_resolve_loader_path', return_value=self.loader),
        ):
            context.start(); self.addCleanup(context.stop)

    def engine(self, cls, text):
        self.path.write_text(text, encoding='utf-8')
        engine = cls(str(self.path)); engine.load_state()
        return engine

    def test_stale_replacements_and_backdated_edits(self):
        for cls, text, key, _ in FIXTURES:
            for replacement in (False, True):
                with self.subTest(engine=cls.__name__, replacement=replacement):
                    engine = self.engine(cls, text); stamp = self.path.stat()
                    if replacement:
                        other = self.home / 'replacement'; other.write_text(text, encoding='utf-8')
                        os.utime(other, ns=(stamp.st_atime_ns, stamp.st_mtime_ns)); other.replace(self.path)
                    else:
                        self.path.write_text(text+' ', encoding='utf-8')
                        os.utime(self.path, ns=(stamp.st_atime_ns, stamp.st_mtime_ns-10_000_000))
                    before = self.path.read_bytes()
                    self.assertFalse(engine.write_value(key, 'DEFAULT', '2', 'int')[0])
                    self.assertEqual(self.path.read_bytes(), before)

    def test_missing_file_clears_cache(self):
        for cls, text, _, _ in FIXTURES[:3]:
            with self.subTest(engine=cls.__name__):
                engine = self.engine(cls, text); self.path.unlink()
                self.assertEqual(dict(engine.load_state()), {})
                self.assertEqual(dict(engine.cache), {})

    def test_write_without_load_and_cache_refresh(self):
        for cls, text, key, lookup in FIXTURES[:3]:
            with self.subTest(engine=cls.__name__):
                self.path.write_text(text, encoding='utf-8'); engine = cls(str(self.path))
                result = engine.write_value(key, 'DEFAULT', '2', 'int')
                self.assertTrue(result[0], result)
                self.assertEqual(str(engine.cache[lookup]), '2')

    def test_atomic_failures_preserve_files_and_remove_staging(self):
        for cls, text, key, _ in FIXTURES:
            for failure in ('os.replace', 'os.fsync'):
                with self.subTest(engine=cls.__name__, failure=failure):
                    engine = self.engine(cls, text); before = set(self.home.iterdir())
                    with patch(failure, side_effect=OSError('fixture I/O failure')):
                        result = engine.write_value(key, 'DEFAULT', '2', 'int')
                    self.assertFalse(result[0], result)
                    self.assertEqual(self.path.read_text(encoding='utf-8'), text)
                    self.assertEqual(set(self.home.iterdir()), before)

    def test_parallel_distinct_writes(self):
        for cls, text, _, _ in FIXTURES[:3]:
            with self.subTest(engine=cls.__name__):
                engine = self.engine(cls, text)
                def write(index):
                    return engine.write_value(f'K{index}', 'DEFAULT', str(index), 'int')
                with ThreadPoolExecutor(max_workers=8) as pool:
                    results = list(pool.map(write, range(128)))
                self.assertTrue(all(r[0] for r in results), results)
                state = engine.load_state()
                for index in range(128):
                    lookup = f'K{index}' if cls is JsonEngine else f'DEFAULT/K{index}'
                    self.assertEqual(str(state[lookup]), str(index))

    def test_cmdline_quotes_duplicates_and_init_boundary(self):
        engine = self.engine(CmdlineEngine, 'x=1 x=2 -- initarg=keep\n')
        result = engine.write_batch([('x:1', 'DEFAULT', '3', 'int'), ('x', 'DEFAULT', 'hello world', 'string'), ('new', 'DEFAULT', '4', 'int')])
        self.assertTrue(result[0], result)
        content = self.path.read_text(encoding='utf-8')
        self.assertIn('x=3 x="hello world"', content)
        self.assertIn('new=4 -- initarg=keep', content)
        self.assertNotIn('DEFAULT/initarg', dict(engine.load_state()))

    def test_invalid_index_and_multiline_abort_batch(self):
        for cls, text, key, _ in FIXTURES[:2]:
            for invalid_key, value in ((key+':9', '2'), (key, 'one\ntwo'), (key+':0', '2')):
                with self.subTest(engine=cls.__name__, key=invalid_key):
                    engine = self.engine(cls, text)
                    self.assertFalse(engine.write_batch([(key, 'DEFAULT', '3', 'int'), (invalid_key, 'DEFAULT', value, 'string')])[0])
                    self.assertEqual(self.path.read_text(encoding='utf-8'), text)

    def test_env_random_values_through_real_bash(self):
        rng = random.Random(315); alphabet = "abcXYZ012 #$`'\"\\;|&()[]*?é😀\t"
        engine = self.engine(ShellEnvEngine, 'export X="old" # keep\n')
        for _ in range(250):
            value = ''.join(rng.choices(alphabet, k=60))
            result = engine.write_value('X', 'DEFAULT', value)
            self.assertTrue(result[0], result)
            actual = subprocess.run(['bash', '--noprofile', '--norc', '-c', '. "$1"; printf %s "$X"', 'fixture', str(self.path)],
                                    check=True, capture_output=True, text=True, timeout=5)
            self.assertEqual(actual.stdout, value)
            self.assertEqual(engine.load_state()['DEFAULT/X'], value)
            self.assertIn('# keep', self.path.read_text(encoding='utf-8'))

    def test_env_single_quote_backslash_comment(self):
        engine = self.engine(ShellEnvEngine, "X='abc\\' # keep\n")
        self.assertTrue(engine.write_value('X', 'DEFAULT', "O'Brien")[0])
        self.assertIn('# keep', self.path.read_text(encoding='utf-8'))
        self.assertEqual(engine.load_state()['DEFAULT/X'], "O'Brien")

    def test_env_crlf_and_explicit_expansion(self):
        self.path.write_bytes(b'export X=1\r\nY=2\r\n')
        engine = ShellEnvEngine(str(self.path)); engine.load_state()
        self.assertTrue(engine.write_value('X', 'DEFAULT', '__VAR__$HOME/bin')[0])
        self.assertEqual(self.path.read_bytes().count(b'\r\n'), 2)
        normalized = self.home / 'env'; normalized.write_bytes(self.path.read_bytes().replace(b'\r\n', b'\n'))
        result = subprocess.run(['bash', '-c', '. "$1"; printf %s "$X"', 'fixture', str(normalized)],
                                check=True, capture_output=True, text=True, timeout=5)
        self.assertEqual(result.stdout, str(self.home / 'bin'))

    def test_json_delete_slash_scope_and_nonfinite_rejection(self):
        engine = self.engine(JsonEngine, '{"a.b":{"c":{"x":1}}}')
        self.assertTrue(engine.write_value('x', 'a.b/c', '2', 'int')[0])
        self.assertEqual(json.loads(self.path.read_text())['a.b']['c']['x'], 2)
        self.assertTrue(engine.write_value('x', 'a.b/c', '__DELETE__')[0])
        self.assertNotIn('x', json.loads(self.path.read_text())['a.b']['c'])
        before = self.path.read_bytes()
        self.assertFalse(engine.write_value('x', 'DEFAULT', 'NaN', 'float')[0])
        self.assertEqual(self.path.read_bytes(), before)

    def test_boot_root_load_never_writes(self):
        with patch('os.geteuid', return_value=0), patch.object(SystemdBootEngine, 'auto_synthesize_all_missing_entries') as synthesize:
            self.engine(SystemdBootEngine, 'title Fixture\noptions x=1\n')
        synthesize.assert_not_called()

    def test_boot_explicit_missing_path(self):
        self.assertEqual(SystemdBootEngine(str(self.path)).config_path, self.path)

    def test_boot_initrd_index_and_repeated_batch(self):
        engine = self.engine(SystemdBootEngine, 'title Fixture\ninitrd /microcode\ninitrd /ramdisk\noptions x=1\n')
        result = engine.write_batch([('initrd:1', 'ENTRY', '/newcode', 'string'), ('x', 'DEFAULT', '2', 'int'), ('x', 'DEFAULT', '3', 'int')])
        self.assertTrue(result[0], result); text = self.path.read_text()
        for expected in ('initrd /newcode', 'initrd /ramdisk', 'x=3'):
            self.assertIn(expected, text)
        self.assertNotIn('x=2', text)

    def test_boot_invalid_scope_and_unknown_override(self):
        for key, scope in (('x', 'OTHER'), ('title__missing', 'ENTRY_OVERRIDE')):
            engine = self.engine(SystemdBootEngine, 'title Fixture\noptions x=1\n'); before = self.path.read_bytes()
            self.assertFalse(engine.write_value(key, scope, '2')[0]); self.assertEqual(self.path.read_bytes(), before)

    def monitor(self, text):
        context = patch('subprocess.run', wraps=subprocess.run); run = context.start(); self.addCleanup(context.stop)
        real = run._mock_wraps
        run.side_effect = lambda args, **kw: subprocess.CompletedProcess(args, 0, '[]', '') if args[0] == 'hyprctl' else real(args, **kw)
        return self.engine(MonitorLuaEngine, text)

    def test_monitor_failed_batch_preserves_source(self):
        text = 'hl.monitor({output="DP-9", mode="preferred"})\n'; engine = self.monitor(text)
        result = engine.write_batch([('mirror', 'monitor/DP-9', 'DP-8', 'string'), ('x', 'unknown', '2', 'int')])
        self.assertFalse(result[0], result); self.assertEqual(self.path.read_text(), text)

    def test_monitor_nested_fields_globals_and_new_blocks(self):
        engine = self.monitor('hl.monitor({output="DP-9", reserved_area={top=3}, mode="preferred"})\n')
        for key, scope, value, kind in [('mirror', 'monitor/DP-9', 'DP-8', 'string'), ('vfr', 'debug', 'false', 'bool'),
                                       ('mode', 'monitor/DP-8', 'preferred', 'string'), ('monitor', 'workspace_rule/2', 'DP-8', 'string')]:
            result = engine.write_value(key, scope, value, kind); self.assertTrue(result[0], result)
            self.assertEqual(str(engine.load_state()[scope+'/'+key]).lower(), value.lower())

    def test_scale_grid_precision_and_rounding(self):
        for width, height in ((1920, 1080), (3840, 2160), (3440, 1440), (5120, 2880), (2256, 1504)):
            for requested in (0.51, 1.001, 1.251, 1.666, 2.1, 3.14):
                self.assertGreaterEqual(clean_scale(width, height, requested)+1e-5, requested)
            for scale in grid_scales(width, height):
                self.assertTrue(is_sharp(width, height, float(format_scale(scale))))
        self.assertFalse(is_sharp(1920, 1080, math.nan)); self.assertFalse(is_sharp(1920, 1080, math.inf))

class AdditionalAuditTests(unittest.TestCase):
    setUp = EngineAuditTests.setUp
    engine = EngineAuditTests.engine
    monitor = EngineAuditTests.monitor
    def test_permissions_symlinks_and_competing_instances(self):
        for cls, text, key, _ in FIXTURES[:3]:
            with self.subTest(engine=cls.__name__):
                engine = self.engine(cls, text)
                self.path.chmod(0o640)
                link = self.home / 'link'
                link.unlink(missing_ok=True); link.symlink_to(self.path)
                first, second = cls(str(link)), cls(str(link))
                first.load_state(); second.load_state()
                self.assertTrue(first.write_value(key, 'DEFAULT', '2', 'int')[0])
                self.assertTrue(link.is_symlink())
                self.assertEqual(self.path.stat().st_mode & 0o777, 0o640)
                self.assertFalse(second.write_value(key, 'DEFAULT', '3', 'int')[0])

    def test_privileged_fallback_uses_atomic_helper(self):
        from python.shared.config_io import atomic_write
        for cls, text, key, module in (
            (CmdlineEngine, 'x=1\n', 'x', 'cmdline'),
            (ShellEnvEngine, 'X=1\n', 'X', 'environment_variables'),
            (SystemdBootEngine, 'title Fixture\noptions x=1\n', 'x', 'systemd_boot'),
        ):
            with self.subTest(engine=cls.__name__):
                engine = self.engine(cls, text)
                with patch(f'python.engines.{module}.atomic_write', side_effect=PermissionError('fixture')):
                    with patch(f'python.engines.{module}.privileged_atomic_write', wraps=atomic_write) as elevated:
                        result = engine.write_value(key, 'DEFAULT', '2', 'int')
                self.assertTrue(result[0], result)
                elevated.assert_called_once()

    def test_multiline_shell_target_is_never_partially_rewritten(self):
        for text in ('X="first\nsecond"\n', 'X=first' + chr(92) + '\nsecond\n'):
            engine = self.engine(ShellEnvEngine, text)
            self.assertFalse(engine.write_value('X', 'DEFAULT', 'replacement')[0])
            self.assertEqual(self.path.read_text(), text)

    def test_kernel_release_uses_metadata_and_never_reads_vmlinuz_as_text(self):
        from python.engines.systemd_boot import resolve_kver_for_pkgbase
        modules = self.home / 'modules'; modules.mkdir()
        presets = self.home / 'presets'; presets.mkdir()
        image = self.home / 'vmlinuz'; image.write_bytes(b'\x00\xffkernel\n')
        (presets / 'linux.preset').write_text(f'ALL_kver="{image}"\n')
        real_path = Path
        def paths(value):
            return modules if value == '/usr/lib/modules' else presets if value == '/etc/mkinitcpio.d' else real_path(value)
        with patch('python.engines.systemd_boot.Path', side_effect=paths):
            self.assertEqual(resolve_kver_for_pkgbase('linux'), '')
            wrong = modules / '7.3.0-wrong-linux'; wrong.mkdir()
            (wrong / 'pkgbase').write_text('another-package\n')
            self.assertEqual(resolve_kver_for_pkgbase('linux'), '')
            correct = modules / '7.3.0-custom'; correct.mkdir()
            (correct / 'pkgbase').write_text('linux\n')
            self.assertEqual(resolve_kver_for_pkgbase('linux'), '7.3.0-custom')

    def test_autostart_hardware_is_discovered(self):
        drm = self.home / 'drm'; card = drm / 'card7'; (card / 'device').mkdir(parents=True)
        (card / 'device/vendor').write_text('0x1002\n')
        block = self.home / 'block'; disk = block / 'sda'; (disk / 'device').mkdir(parents=True)
        real_path = Path
        def paths(value):
            return drm if value == '/sys/class/drm' else block if value == '/sys/class/block' else real_path(value)
        with patch('python.engines.autostart_engine.Path', side_effect=paths) as path_class:
            path_class.home.return_value = self.home
            gpu = AutostartLuaEngine._canonical_command('glance_gpu_usage', AUTOSTART_DEFAULTS['autostart/glance_gpu_usage'])
            drive = AutostartLuaEngine._canonical_command('glance_disk_read', AUTOSTART_DEFAULTS['autostart/glance_disk_read'])
        self.assertIn('--gpu-usage card7 amd', gpu)
        self.assertIn('--disk-read sda', drive)
        self.assertNotIn('nvme0n1', drive)

    def test_monitor_existing_include_bindings_remain_editable(self):
        included = self.home / 'included.lua'; included.write_text('hl.config({input={sensitivity=0}})\n')
        engine = self.monitor('dofile("included.lua")\n')
        result = engine.write_value('sensitivity', 'input', '0.5', 'float')
        self.assertTrue(result[0], result)
        self.assertEqual(engine.load_state()['input/sensitivity'], 0.5)

    def test_monitor_stale_file_and_invalid_number_leave_original(self):
        engine = self.monitor('hl.monitor({output="DP-9", mode="preferred"})\n')
        self.path.write_text('-- changed\n'+self.path.read_text())
        before = self.path.read_bytes()
        self.assertFalse(engine.write_value('mirror', 'monitor/DP-9', 'DP-8')[0])
        self.assertEqual(self.path.read_bytes(), before)
        engine.load_state()
        self.assertFalse(engine.write_value('scale', 'monitor/DP-9', 'NaN', 'float')[0])
        self.assertEqual(self.path.read_bytes(), before)

    def test_loader_globs_preserved_and_deletion_does_not_synthesize(self):
        engine = self.engine(SystemdBootEngine, 'title Fixture\noptions x=1\n')
        with patch.object(engine, 'ensure_entry_exists') as synthesize, patch('subprocess.run', return_value=subprocess.CompletedProcess([], 0, '', '')):
            self.assertTrue(engine.write_value('default', 'LOADER', 'arch*.conf')[0])
            self.assertIn('arch*.conf', self.loader.read_text())
            self.assertTrue(engine.write_value('default', 'LOADER', '__DELETE__')[0])
            self.assertNotIn('default', self.loader.read_text())
        synthesize.assert_not_called()

if __name__ == '__main__':
    unittest.main()
