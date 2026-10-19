"""Focused regressions and protocol checks using disposable files and processes."""
import array
import importlib.util
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

SOURCE = Path(os.environ.get('RUNNER_AUDIT_SOURCE') or
              Path(__file__).resolve().parents[1] / 'master_runner.py')
spec = importlib.util.spec_from_file_location('runner_audit_target', SOURCE)
r = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = r
spec.loader.exec_module(r)
r.Log.level = r.Verbosity.QUIET


class AuditTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory(prefix='runner-audit-')
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        (self.root / 'profiles').mkdir()
        (self.root / 'profiles/game.toml').write_text(
            '[paths]\ngame_dir = ' + json.dumps(str(self.root)) + '\n', encoding='utf-8')
        self.mgr = r.ProfileManager(self.root)

    def load_config(self, text):
        (self.root / 'config.toml').write_text(text, encoding='utf-8')
        return r.ProfileManager(self.root).load('game')

    def test_invalid_section_shapes_are_configuration_errors(self):
        for config in ('runtime = 7', 'runtime = "wine"', 'runtime = [1]',
                       '[runtime]\nwine = 7', '[graphics]\ngamescope = false'):
            with self.subTest(config=config), self.assertRaises(r.ConfigError):
                self.load_config(config)

    def test_array_override_preserves_quoted_values(self):
        self.assertEqual(r.coerce_scalar('["a,b", "true", "12", "a b", "λ"]'),
                         ['a,b', 'true', '12', 'a b', 'λ'])

    def test_invalid_array_override_is_configuration_error(self):
        with self.assertRaises(r.ConfigError):
            r.coerce_scalar('["unterminated]')

    def test_array_elements_are_validated(self):
        for text in ('[hooks]\npre_launch = [7]',
                     '[runtime.wine]\nwinetricks = [false]',
                     '[runtime.wine]\nredistributables = [{}]',
                     '[graphics.gamescope]\nextra_args = [1]'):
            with self.subTest(config=text), self.assertRaises(r.ConfigError):
                self.load_config(text)

    def test_numeric_domains_are_validated(self):
        for text in ('[audio]\nrate = -1', '[audio]\nquantum = -2',
                     '[graphics.gamescope]\nwidth = -1',
                     '[storage]\ndwarfs_workers = -1',
                     '[graphics]\nshader_cache_size_gb = -1'):
            with self.subTest(config=text), self.assertRaises(r.ConfigError):
                self.load_config(text)

    def test_empty_command_has_nonthrowing_error_result(self):
        self.assertEqual(r.run_cmd([]).rc, 127)

    def test_helper_uses_supplied_path(self):
        command = self.root / 'audit-helper'
        command.write_text('#!/bin/sh\nprintf correct-path', encoding='utf-8')
        command.chmod(0o700)
        result = r.run_cmd(['audit-helper'], env=dict(os.environ, PATH=str(self.root)))
        self.assertEqual(result.rc, 0, result.err)
        self.assertEqual(result.out, 'correct-path')

    def test_zero_available_memory_is_preserved(self):
        with patch.object(r, 'read_text', return_value='MemTotal: 16384 kB\nMemAvailable: 0 kB\n'):
            self.assertEqual(r.MemInfo.read().available_kib, 0)

    def test_gpu_selector_is_case_and_whitespace_insensitive(self):
        intel = r.Gpu('card0', '', '', '0000:00:02.0', 0x8086, 1, '', 'i915', True, '', 'Intel')
        nvidia = r.Gpu('card1', '', '', '0000:01:00.0', 0x10DE, 2, '', 'nvidia', False, '', 'NVIDIA')
        with patch.object(r, 'gpus', return_value=(intel, nvidia)):
            self.assertIs(r.select_gpu(' INTEGRATED '), intel)

    def test_prefix_architecture_mismatch_is_rejected_before_changes(self):
        prefix = r.WinePrefix(self.root / 'prefix', arch='win64')
        prefix.path.mkdir()
        (prefix.path / 'system.reg').write_text('WINE REGISTRY Version 2\n\n#arch=win32\n', encoding='utf-8')
        (prefix.path / 'user.reg').write_text('ready', encoding='utf-8')
        with patch.object(r, 'run_cmd') as execute, self.assertRaises(r.ConfigError):
            prefix.provision(root_dir=self.root, redistributables=[], winetricks=[])
        execute.assert_not_called()

    def translator_fixture(self):
        source = self.root / 'data/lutris/runtime/dxvk/2.9/x64'
        source.mkdir(parents=True)
        (source / 'd3d11.dll').write_bytes(b'translator')
        prefix = r.WinePrefix(self.root / 'prefix')
        system = prefix.drive_c / 'windows/system32'
        system.mkdir(parents=True)
        dll = system / 'd3d11.dll'
        dll.write_bytes(b'original')
        return prefix, source, dll

    def test_disabling_removed_translator_restores_original(self):
        prefix, source, dll = self.translator_fixture()
        with patch.object(r, 'XDG_DATA_HOME', self.root / 'data'):
            prefix.link_translators(want_vkd3d=False)
            dll.unlink()  # Upgrade repair may already have removed a dangling link.
            prefix.link_translators(want_dxvk=False, want_vkd3d=False)
        self.assertEqual(dll.read_bytes(), b'original')

    def test_failed_dll_publish_restores_original(self):
        prefix, source, dll = self.translator_fixture()
        with patch.object(r, 'XDG_DATA_HOME', self.root / 'data'), \
             patch.object(Path, 'symlink_to', side_effect=OSError('injected link failure')):
            with self.assertRaises(OSError):
                prefix.link_translators(want_vkd3d=False)
        self.assertEqual(dll.read_bytes(), b'original')

    def test_missing_translator_does_not_silently_succeed(self):
        prefix, source, dll = self.translator_fixture()
        (source / 'd3d11.dll').unlink()
        with patch.object(r, 'XDG_DATA_HOME', self.root / 'data'), \
             patch.object(r.Path, 'is_dir', autospec=True, side_effect=lambda p: p == dll.parent or p == source), \
             self.assertRaises(r.ConfigError):
            prefix.link_translators(want_vkd3d=False)

    def test_toml_serialization_handles_control_characters(self):
        value = ''.join(map(chr, range(32))) + '\x7fline\nbreak\t"slash\\λ'
        self.assertEqual(r.tomllib.loads('name = "' + r._toml_str(value) + '"')['name'], value)

    def test_winetricks_uses_the_selected_wine_build(self):
        prefix = r.WinePrefix(self.root / 'prefix', wine_bin='/custom/build/bin/wine')
        with patch.object(r, 'have', return_value=True), \
             patch.object(r, 'run_cmd', return_value=r.Ran(0, '', '')) as execute:
            prefix._run_winetricks({}, self.root, ['vcrun2022'])
        argv = execute.call_args_list[0].args[0]
        env = execute.call_args_list[0].kwargs['env']
        self.assertEqual(argv, ['winetricks', '--unattended', 'vcrun2022'])
        self.assertEqual(env['WINE'], '/custom/build/bin/wine')
        self.assertEqual(env['WINESERVER'], prefix.server_bin)

    def test_provisioning_order_changes_invalidate_stamp(self):
        for field in ('redistributables', 'winetricks'):
            with self.subTest(field=field):
                prefix = r.WinePrefix(self.root / field)
                prefix.path.mkdir()
                for name in ('system.reg', 'user.reg'):
                    (prefix.path / name).write_text('ready', encoding='utf-8')
                arguments = dict(root_dir=self.root, redistributables=[], winetricks=[])
                arguments[field] = ['second', 'first']
                with patch.object(r, 'RUNTIME_DIR', self.root / 'runtime'), \
                     patch.object(r.WinePrefix, 'prune_broken_symlinks'), \
                     patch.object(r.WinePrefix, 'unify_user_dirs'), \
                     patch.object(r.WinePrefix, 'suppress_crash_dialogs'), \
                     patch.object(r.WinePrefix, 'clean_stale_crash_markers'), \
                     patch.object(r.WinePrefix, 'link_translators'), \
                     patch.object(r.WinePrefix, 'serverwait'), \
                     patch.object(r.WinePrefix, '_install_redists') as redists, \
                     patch.object(r.WinePrefix, '_run_winetricks') as tricks:
                    prefix.provision(**arguments)
                    prefix.provision(**arguments)
                    self.assertEqual((redists if field == 'redistributables' else tricks).call_count, 1)
                    arguments[field].reverse()
                    prefix.provision(**arguments)
                    self.assertEqual((redists if field == 'redistributables' else tricks).call_count, 2)

    def test_proton_runtime_storage_is_independent_of_game_home(self):
        profile = self.mgr.load('game', overrides={
            'runtime': {'type': 'umu'}, 'env': {'HOME': str(self.root / 'game-home')}})
        builder = r.EnvironmentBuilder(profile, r.resolve_paths(profile), dry_run=True)
        builder.stage_profile_env()
        with patch.object(r, 'have', return_value=True):
            builder.stage_wine(dry_run=True, under_gamescope=False)
        self.assertEqual(builder.env['UMU_FOLDERS_PATH'], str(r.XDG_DATA_HOME))

    def test_umu_rejects_silently_ignored_provisioning(self):
        with self.assertRaises(r.ConfigError):
            self.mgr.load('game', overrides={'runtime': {'type': 'umu', 'wine': {'winetricks': ['vcrun2022']}}})

    def test_wayland_backends_are_consistent_inside_gamescope(self):
        profile = self.mgr.load('game', overrides={'runtime': {'type': 'wine'}})
        builder = r.EnvironmentBuilder(profile, r.resolve_paths(profile), dry_run=True)
        builder.stage_session(under_gamescope=True)
        self.assertNotIn('DISPLAY', builder.env)
        for variable in ('SDL_VIDEODRIVER', 'GDK_BACKEND', 'QT_QPA_PLATFORM'):
            self.assertEqual(builder.env[variable], 'wayland')
        self.assertEqual(builder.env['PROTON_ENABLE_WAYLAND'], '1')

    def test_automatic_gamescope_gpu_uses_primary_consistently(self):
        intel = r.Gpu('card0', '', '', '0000:00:02.0', 0x8086, 1, '', 'i915', True, '', 'Intel')
        nvidia = r.Gpu('card1', '', '', '0000:01:00.0', 0x10DE, 2, '', 'nvidia', False, '', 'NVIDIA')
        profile = self.mgr.load('game')
        paths = r.resolve_paths(profile)
        with patch.object(r, 'gpus', return_value=(intel, nvidia)), \
             patch.object(r, 'vulkan_icds', return_value=()):
            builder = r.EnvironmentBuilder(profile, paths, dry_run=True)
            builder.stage_gpu(under_gamescope=True)
            self.assertIs(builder.gpu, intel)
            argv = r.PipelineBuilder(profile, paths, []).gamescope_argv()
            self.assertEqual(argv[argv.index('--prefer-vk-device') + 1], intel.vk_device_select)

    def test_explicit_gamescope_gpu_choice_is_preserved(self):
        intel = r.Gpu('card0', '', '', '0000:00:02.0', 0x8086, 1, '', 'i915', True, '', 'Intel')
        nvidia = r.Gpu('card1', '', '', '0000:01:00.0', 0x10DE, 2, '', 'nvidia', False, '', 'NVIDIA')
        with patch.object(r, 'gpus', return_value=(intel, nvidia)), \
             patch.object(r, 'vulkan_icds', return_value=()):
            for mode in ('nvidia', 'discrete', '0000:01:00.0'):
                with self.subTest(mode=mode):
                    profile = self.mgr.load('game', overrides={'graphics': {'gpu': mode}})
                    paths = r.resolve_paths(profile)
                    builder = r.EnvironmentBuilder(profile, paths, dry_run=True)
                    builder.stage_gpu(under_gamescope=True)
                    self.assertIs(builder.gpu, nvidia)
                    argv = r.PipelineBuilder(profile, paths, []).gamescope_argv()
                    self.assertEqual(argv[argv.index('--prefer-vk-device') + 1], nvidia.vk_device_select)

    def test_automatic_direct_launch_still_prefers_discrete(self):
        intel = r.Gpu('card0', '', '', '0000:00:02.0', 0x8086, 1, '', 'i915', True, '', 'Intel')
        nvidia = r.Gpu('card1', '', '', '0000:01:00.0', 0x10DE, 2, '', 'nvidia', False, '', 'NVIDIA')
        profile = self.mgr.load('game')
        with patch.object(r, 'gpus', return_value=(intel, nvidia)), \
             patch.object(r, 'vulkan_icds', return_value=()):
            builder = r.EnvironmentBuilder(profile, r.resolve_paths(profile), dry_run=True)
            builder.stage_gpu(under_gamescope=False)
            self.assertIs(builder.gpu, nvidia)

    def test_no_scope_wait_is_event_driven(self):
        selector = r.selectors.DefaultSelector()
        original = selector.select
        timeouts = []
        def select(timeout=None):
            timeouts.append(timeout)
            return original(timeout)
        selector.select = select
        child = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(.15)'],
                                 process_group=0)
        self.addCleanup(child.wait)
        with patch.object(r.selectors, 'DefaultSelector', return_value=selector):
            self.assertEqual(r.Supervisor(child).wait(), 0)
        self.assertEqual(timeouts, [None])

    def test_partial_translator_failure_keeps_completed_links_tracked(self):
        prefix, source, dll = self.translator_fixture()
        with patch.object(r, 'XDG_DATA_HOME', self.root / 'data'), \
             patch.object(r.Path, 'is_dir', autospec=True,
                          side_effect=lambda p: p in (source.parent.parent, source.parent,
                                                      source, dll.parent)), \
             self.assertRaises(r.ConfigError):
            prefix.link_translators(want_vkd3d=True)
        managed = json.loads((prefix.path / '.master-runner-dlls.json').read_text(encoding='utf-8'))
        self.assertEqual(managed[str(dll)], str((source / 'd3d11.dll').resolve()))

    def test_affinity_large_range_is_bounded_by_usable_cpus(self):
        with patch.object(r.os, 'sched_getaffinity', return_value={0, 3, 7}):
            self.assertEqual(r.parse_affinity('0-1000000000000'), [0, 3, 7])


class DBusTests(unittest.TestCase):
    def test_marshalling_roundtrips(self):
        for signature, value in [('s', 'λ'), ('a{sv}', {'a': ('s', 'λ'), 'b': ('u', 42)}),
                                 ('a(usu)', [(1, 'one', 2), (3, 'three', 4)]),
                                 ('as', []), ('ay', list(range(255))), ('(yxt)', (255, -123, 456))]:
            with self.subTest(signature=signature):
                writer = r._Writer()
                writer.write(signature, value)
                observed = r._Reader(bytes(writer.buf)).read(signature)
                expected = {'a': 'λ', 'b': 42} if signature == 'a{sv}' else value
                self.assertEqual(observed, expected)

    def test_real_session_bus_protocol(self):
        connection = r.DBusConnection(r.session_bus_address())
        self.addCleanup(connection.close)
        (names,) = connection.call('org.freedesktop.DBus', '/org/freedesktop/DBus',
                                   'org.freedesktop.DBus', 'ListNames', '', (), reply_sig='as')
        self.assertIn(connection.unique_name, names)

    def test_unclaimed_received_fds_are_closed(self):
        left, right = socket.socketpair()
        self.addCleanup(left.close)
        self.addCleanup(right.close)
        conn = object.__new__(r.DBusConnection)
        conn._sock, conn._serial, conn.unique_name = left, 0, ''
        read_fd, write_fd = os.pipe()
        self.addCleanup(os.close, read_fd)
        self.addCleanup(os.close, write_fd)
        writer = r._Writer()
        writer.buf += r.struct.pack('<BBBBII', ord('l'), 2, 0, 1, 0, 999)
        writer.write('a(yv)', [(5, ('u', 1)), (9, ('u', 1))])
        writer.align(8)
        right.sendmsg([bytes(writer.buf)], [(socket.SOL_SOCKET, socket.SCM_RIGHTS, array.array('i', [read_fd]))])
        before = set(os.listdir('/proc/self/fd'))
        self.assertEqual(conn.call('dest', '/path', 'interface', 'member', '', ()), ())
        self.assertEqual(set(os.listdir('/proc/self/fd')), before)

    def test_trickled_reply_obeys_total_timeout(self):
        left, right = socket.socketpair()
        self.addCleanup(left.close)
        self.addCleanup(right.close)
        conn = object.__new__(r.DBusConnection)
        conn._sock, conn._serial, conn.unique_name = left, 0, ''
        def trickle():
            try:
                for _ in range(12):
                    right.sendall(b'l')
                    time.sleep(.03)
            except OSError:
                pass
        worker = threading.Thread(target=trickle)
        worker.start()
        started = time.monotonic()
        with self.assertRaises((r.DBusError, TimeoutError, OSError)):
            conn.call('dest', '/path', 'interface', 'member', '', (), timeout=.08)
        elapsed = time.monotonic() - started
        left.close()
        worker.join(timeout=1)
        self.assertLess(elapsed, .25)


if __name__ == '__main__':
    unittest.main()
