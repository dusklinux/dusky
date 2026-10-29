"""Adversarial second-pass regressions for the first audit's changes."""
import json
import os
import subprocess
import unittest
from unittest.mock import patch

from python.tests import test_engine_audit as audit_tests
from python.engines.cmdline import CmdlineEngine
from python.engines.monitor_engine import MonitorLuaEngine
from python.engines.systemd_boot import SystemdBootEngine
from python.engines.environment_variables import ShellEnvEngine
from python.engines.json_engine import JsonEngine


class SecondPassTests(unittest.TestCase):
    setUp = audit_tests.EngineAuditTests.setUp
    engine = audit_tests.EngineAuditTests.engine
    monitor = audit_tests.EngineAuditTests.monitor

    def test_missing_cmdline_aliases_address_one_occurrence(self):
        engine = self.engine(CmdlineEngine, 'quiet\n')
        result = engine.write_batch([('x', 'DEFAULT', '1', 'int'), ('x:1', 'DEFAULT', '2', 'int')])
        self.assertTrue(result[0], result)
        self.assertEqual(self.path.read_text().split().count('x=2'), 1)
        self.assertNotIn('x=1', self.path.read_text())

    def test_monitor_auto_keeps_native_string(self):
        engine = self.monitor('hl.monitor({output="DP-9",scale=1})\n')
        self.assertTrue(engine.write_value('scale', 'monitor/DP-9', 'auto', 'picker')[0])
        self.assertEqual(engine.load_state()['monitor/DP-9/scale'], 'auto')

    def test_monitor_numeric_deletion_remains_supported(self):
        engine = self.monitor('hl.monitor({output="DP-9",scale=1})\n')
        self.assertTrue(engine.write_value('scale', 'monitor/DP-9', '__DELETE__', 'float')[0])
        self.assertIn('scale=nil', self.path.read_text().replace(' ', ''))

    def test_monitor_scale_uses_requested_mode(self):
        engine = self.monitor('hl.monitor({output="DP-9",mode="1920x1080",scale=1})\n')
        engine._monitor_resolutions['DP-9'] = (1920, 1080)
        result = engine.write_batch([('scale', 'monitor/DP-9', '1.5', 'float'), ('mode', 'monitor/DP-9', '2560x1440', 'string')])
        self.assertTrue(result[0], result)
        self.assertEqual(engine.load_state()['monitor/DP-9/scale'], 1.6)

    def test_boot_init_arguments_are_not_kernel_state(self):
        engine = self.engine(SystemdBootEngine, 'title Fixture\noptions x=1 -- initarg=keep\n')
        self.assertNotIn('DEFAULT/initarg', dict(engine.cache))
        self.assertNotIn('DEFAULT/--', dict(engine.cache))

    def test_boot_target_selection_does_not_write_loader(self):
        engine = self.engine(SystemdBootEngine, 'title Fixture\noptions x=1\n')
        before = self.loader.read_bytes()
        with patch.object(engine, 'ensure_entry_exists', return_value=(True, self.path, 'exists')), \
             patch.object(engine, '_resolve_config_path', return_value=self.path), \
             patch.object(engine, '_atomic_write', wraps=engine._atomic_write) as commit:
            result = engine.write_value('target_entry', 'LOADER', 'Fixture')
        self.assertTrue(result[0], result)
        commit.assert_not_called()
        self.assertEqual(self.loader.read_bytes(), before)

    def test_boot_failed_target_selection_preserves_selection(self):
        engine = self.engine(SystemdBootEngine, 'title Fixture\noptions x=1\n')
        with patch.object(engine, 'ensure_entry_exists', return_value=(False, None, 'fixture failure')):
            self.assertFalse(engine.write_value('target_entry', 'LOADER', 'Missing')[0])
        self.assertEqual(engine._target_override, '')

    def test_boot_metadata_aliases_do_not_append_duplicate_directives(self):
        engine = self.engine(SystemdBootEngine, 'title Fixture\ninitrd /microcode\ninitrd /ramdisk\noptions x=1\n')
        result = engine.write_batch([('initrd', 'ENTRY', '/first', 'string'), ('initrd:2', 'ENTRY', '/last', 'string')])
        self.assertTrue(result[0], result)
        self.assertEqual([line for line in self.path.read_text().splitlines() if line.startswith('initrd')],
                         ['initrd /microcode', 'initrd /last'])

    def test_boot_firmware_failure_reports_persisted_configuration(self):
        engine = self.engine(SystemdBootEngine, 'title Fixture\noptions x=1\n')
        with patch('subprocess.run', return_value=subprocess.CompletedProcess([], 1, '', 'fixture EFI failure')):
            result = engine.write_value('timeout', 'LOADER', '5')
        self.assertFalse(result[0], result)
        self.assertIn('saved', result[1].lower())
        self.assertIn('fixture EFI failure', result[1])
        self.assertIn('5', self.loader.read_text())

    def test_boot_rejects_invalid_cmdline_before_loader_commit(self):
        engine = self.engine(SystemdBootEngine, 'title Fixture\noptions x=1\n')
        before = self.loader.read_bytes(), self.path.read_bytes()
        with patch('subprocess.run', return_value=subprocess.CompletedProcess([], 0, '', '')):
            result = engine.write_batch([('timeout', 'LOADER', '5', 'int'), ('bad=key', 'DEFAULT', '2', 'int')])
        self.assertFalse(result[0], result)
        self.assertEqual((self.loader.read_bytes(), self.path.read_bytes()), before)

    def test_boot_rejects_conflicting_options_bindings(self):
        engine = self.engine(SystemdBootEngine, 'title Fixture\noptions x=1\n')
        before = self.path.read_bytes()
        result = engine.write_batch([('options', 'ENTRY', 'y=3', 'string'), ('x', 'DEFAULT', '2', 'int')])
        self.assertFalse(result[0], result)
        self.assertEqual(self.path.read_bytes(), before)

    def test_boot_load_snapshot_describes_the_bytes_actually_loaded(self):
        engine = self.engine(SystemdBootEngine, 'title Fixture\noptions x=1\n')
        def change_after_loader_read(*args):
            self.loader.write_text('timeout 4\n')
            return self.path
        with patch.object(engine, '_resolve_config_path', side_effect=change_after_loader_read):
            engine.load_state()
        self.assertEqual(engine.cache['LOADER/timeout'], '3')
        with patch('subprocess.run', return_value=subprocess.CompletedProcess([], 0, '', '')):
            self.assertFalse(engine.write_value('timeout', 'LOADER', '5')[0])
        self.assertEqual(self.loader.read_text(), 'timeout 4\n')

    def test_dashboard_generated_in_a_home_with_spaces_is_detected(self):
        from python.engines.autostart_engine import AutostartLuaEngine
        engine = self.engine(AutostartLuaEngine, '')
        command = 'hl.exec_cmd(' + engine._lua_string("'/home/a b/user_scripts/rofi/dusky_glance.sh' --gpu-usage card7 amd") + ')'
        with patch.object(engine, '_canonical_command', return_value=command):
            self.assertTrue(engine.write_value('glance_gpu_usage', 'autostart', 'true', 'bool')[0])
        self.assertTrue(engine.load_state()['autostart/glance_gpu_usage'])

    def test_monitor_injection_with_unrelated_include_is_supported(self):
        child = self.home / 'child.lua'; child.write_text('hl.config({input={sensitivity=0}})\n')
        engine = self.monitor('dofile("child.lua")\nhl.monitor({output="DP-9",mode="preferred"})\n')
        before = child.read_bytes()
        result = engine.write_value('mirror', 'monitor/DP-9', 'DP-8', 'string')
        self.assertTrue(result[0], result)
        self.assertEqual(engine.load_state()['monitor/DP-9/mirror'], 'DP-8')
        self.assertEqual(child.read_bytes(), before)

    def test_hybrid_cross_file_batch_refuses_without_writing_either_file(self):
        from python.engines.autostart_engine import AutostartLuaEngine
        child = self.home / 'child.lua'; child.write_text('hl.config({input={sensitivity=0}})\n')
        engine = self.engine(AutostartLuaEngine, 'dofile("child.lua")\n')
        before = self.path.read_bytes(), child.read_bytes()
        result = engine.write_batch([('waybar', 'autostart', 'true', 'bool'), ('sensitivity', 'input', '0.5', 'float')])
        self.assertFalse(result[0], result)
        self.assertEqual((self.path.read_bytes(), child.read_bytes()), before)

    def test_new_font_file_keeps_the_creating_process_umask(self):
        from python.engines.fontconfig import FontconfigEngine
        original_umask = os.umask(0o077)
        try:
            FontconfigEngine._atomic_write(self.path, '<fontconfig/>')
        finally:
            os.umask(original_umask)
        self.assertEqual(self.path.stat().st_mode & 0o777, 0o600)

    def test_ambiguous_boot_title_slug_cannot_edit_the_wrong_entry(self):
        engine = self.engine(SystemdBootEngine, 'title Fixture\noptions x=1\n')
        other = self.home / 'other.conf'; other.write_text('title Other\noptions x=1\n')
        engine._cached_entries_map = {'Kernel-A': {'entry_path': str(self.path)}, 'Kernel A': {'entry_path': str(other)}}
        before = self.path.read_bytes(), other.read_bytes()
        result = engine.write_value('title__kernel_a', 'ENTRY_OVERRIDE', 'Changed')
        self.assertFalse(result[0], result)
        self.assertEqual((self.path.read_bytes(), other.read_bytes()), before)

    def test_monitor_virtual_enabled_deletion_targets_disabled(self):
        engine = self.monitor('hl.monitor({output="DP-9",disabled=true})\n')
        result = engine.write_value('enabled', 'monitor/DP-9', '__DELETE__', 'bool')
        self.assertTrue(result[0], result)
        self.assertNotIn('disabled=true', self.path.read_text().replace(' ', ''))
        self.assertNotIn('enabled=', self.path.read_text().replace(' ', ''))

    def test_json_serialization_failure_never_creates_staging_file(self):
        engine = self.engine(JsonEngine, '{"x":1}')
        with patch('python.engines.json_engine.json.dumps', side_effect=TypeError('fixture serialization failure')), \
             patch('python.shared.config_io.tempfile.NamedTemporaryFile') as temporary:
            result = engine.write_value('x', 'DEFAULT', '2', 'int')
        self.assertFalse(result[0], result)
        temporary.assert_not_called()
        self.assertEqual(json.loads(self.path.read_text()), {'x': 1})

    def test_shell_literal_backslashes_and_single_quotes(self):
        engine = self.engine(ShellEnvEngine, "X='old'\n")
        for value in ("a'\\'b", '\\', "'", '\\$HOME', '  spaced  '):
            self.assertTrue(engine.write_value('X', 'DEFAULT', value)[0])
            actual = subprocess.run(['bash', '--noprofile', '--norc', '-c', '. "$1"; printf %s "$X"', 'test', str(self.path)],
                                    capture_output=True, check=True, text=True, timeout=5)
            self.assertEqual(actual.stdout, value)
            self.assertEqual(engine.load_state()['DEFAULT/X'], value)

class FinalShellParsingTests(unittest.TestCase):
    setUp = audit_tests.EngineAuditTests.setUp
    engine = audit_tests.EngineAuditTests.engine

    def check_source(self, source):
        engine = self.engine(ShellEnvEngine, source)
        actual = subprocess.run(['bash', '--noprofile', '--norc', '-c', '. "$1"; printf %s "$X"', 'test', str(self.path)],
                                capture_output=True, check=True, text=True, timeout=5)
        self.assertIn('DEFAULT/X', engine.cache)
        self.assertEqual(engine.cache['DEFAULT/X'], actual.stdout)
        result = engine.write_value('Y', 'DEFAULT', 'unrelated')
        self.assertTrue(result[0], result)
        self.assertTrue(self.path.read_text(encoding='utf-8').startswith(source))
        self.assertEqual(engine.load_state()['DEFAULT/X'], actual.stdout)

    def test_hash_in_assignment_word_is_literal(self):
        for source in ('X=#ff0000\n', 'X=first' + chr(92) + ' #literal\n'):
            with self.subTest(source=source):
                self.check_source(source)

    def test_escaped_trailing_space_is_part_of_value(self):
        for source in ('X=first' + chr(92) + ' \n', 'X=first' + chr(92) + '  # comment\n'):
            with self.subTest(source=source):
                self.check_source(source)

    def test_unicode_whitespace_is_part_of_shell_word(self):
        for char in ('\u2028', '\u00a0'):
            with self.subTest(character=repr(char)):
                self.check_source('X=' + char + 'value' + char + '\n')

if __name__ == '__main__':
    unittest.main()
