"""Exercise power actions through stub commands; never stop the real session."""
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest

SCRIPT = Path(__file__).resolve().parents[1] / 'dusky_session.sh'
ZSH_MODULE = SCRIPT.parents[2] / '.config/zshrc/session'


class SessionActions(unittest.TestCase):
    def fixture(self, root):
        commands = {
            'ps': '#!/bin/sh\nprintf "1\\n"\n',
            'hyprctl': '''#!/bin/sh
if [ "$1" = -j ]; then printf '[]\\n'; exit 0; fi
if [ "$2" = 'hl.dsp.exit()' ]; then
 printf 'exit\\n' >> "$FIXTURE_LOG"; printf 'ok\\n'; exit "$FINAL_RC"
fi
printf 'ok\\n'
''',
            'systemctl': '''#!/bin/sh
printf '%s\\n' "$*" >> "$FIXTURE_LOG"
case "$*" in
 '--user is-active --quiet graphical-session.target') exit "$ACTIVE_RC";;
 '--user stop graphical-session.target') exit "$STOP_RC";;
 --no-wall*) exit "$FINAL_RC";;
esac
''',
        }
        for name, source in commands.items():
            path = root / name
            path.write_text(source, encoding='utf-8')
            path.chmod(0o755)
        return os.environ | {'PATH': str(root) + ':' + os.environ['PATH'],
                             'FIXTURE_LOG': str(root / 'calls'), 'ACTIVE_RC': '0',
                             'STOP_RC': '0', 'FINAL_RC': '0'}

    def test_actions_stop_services_before_final_request_and_restore_on_rejection(self):
        for action in ('logout', 'reboot', 'poweroff', 'soft-reboot'):
            for rejected in (False, True):
                with self.subTest(action=action, rejected=rejected), tempfile.TemporaryDirectory() as directory:
                    root = Path(directory)
                    env = self.fixture(root) | {'FINAL_RC': '7' if rejected else '0'}
                    result = subprocess.run(['bash', str(SCRIPT), '--worker', action, str(os.getpid())],
                                            env=env, capture_output=True, text=True, timeout=5)
                    calls = (root / 'calls').read_text().splitlines()
                    final = 'exit' if action == 'logout' else f'--no-wall {action}'
                    self.assertLess(calls.index('--user stop graphical-session.target'), calls.index(final))
                    self.assertEqual(result.returncode, 7 if rejected else 0, result.stderr)
                    self.assertEqual('--user start hyprland-session.target' in calls, rejected)

    def test_stop_failure_cancels_power_action_and_restores_services(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            result = subprocess.run(['bash', str(SCRIPT), '--worker', 'reboot', str(os.getpid())],
                                    env=self.fixture(root) | {'STOP_RC': '8'}, capture_output=True, timeout=5)
            self.assertEqual(result.returncode, 8)
            calls = (root / 'calls').read_text().splitlines()
            self.assertNotIn('--no-wall reboot', calls)
            self.assertEqual(calls[-1], '--user start hyprland-session.target')

    def test_inactive_session_is_not_started_after_rejected_action(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            result = subprocess.run(['bash', str(SCRIPT), '--worker', 'reboot', str(os.getpid())],
                                    env=self.fixture(root) | {'ACTIVE_RC': '3', 'FINAL_RC': '7'},
                                    capture_output=True, timeout=5)
            self.assertEqual(result.returncode, 7)
            calls = (root / 'calls').read_text().splitlines()
            self.assertEqual(calls, ['--user is-active --quiet graphical-session.target', '--no-wall reboot'])

    def test_public_entrypoint_launches_a_collectable_transient_job(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            env = self.fixture(root)
            runner = root / 'systemd-run'
            runner.write_text('''#!/usr/bin/python3
import json, os, sys
from pathlib import Path
Path(os.environ['FIXTURE_LOG']).write_text(json.dumps(sys.argv[1:]))
''', encoding='utf-8')
            runner.chmod(0o755)
            result = subprocess.run(['bash', str(SCRIPT), 'reboot'], env=env, capture_output=True, timeout=5)
            self.assertEqual(result.returncode, 0)
            arguments = json.loads((root / 'calls').read_text())
            for flag in ('--user', '--collect', '--wait', '--service-type=exec', '--expand-environment=no', '--unit=dusky-session-action'):
                self.assertIn(flag, arguments)
            self.assertEqual(arguments[arguments.index('--') + 1:-1],
                             ['/usr/bin/bash', str(SCRIPT), '--worker', 'reboot'])
            self.assertFalse(any('PartOf=' in arg for arg in arguments))

    def test_zsh_routes_only_plain_hyprland_power_commands(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            env = self.fixture(root) | {'HOME': directory, 'HYPRLAND_INSTANCE_SIGNATURE': 'fixture'}
            helper = root / 'user_scripts/wlogout/dusky_session.sh'
            helper.parent.mkdir(parents=True)
            helper.write_text('#!/bin/sh\nprintf "helper %s\\n" "$*" >> "$FIXTURE_LOG"\n', encoding='utf-8')
            helper.chmod(0o755)
            source = 'source "$1"; systemctl reboot; systemctl --user status example; systemctl reboot --force'
            subprocess.run(['zsh', '-f', '-c', source, 'fixture', str(ZSH_MODULE)], env=env, check=True)
            self.assertEqual((root / 'calls').read_text().splitlines(),
                             ['helper reboot', '--user status example', 'reboot --force'])
            (root / 'calls').unlink()
            env['HYPRLAND_INSTANCE_SIGNATURE'] = ''
            subprocess.run(['zsh', '-f', '-c', 'source "$1"; systemctl reboot', 'fixture', str(ZSH_MODULE)],
                           env=env, check=True)
            self.assertEqual((root / 'calls').read_text().strip(), 'reboot')

    def test_relative_entrypoint_passes_an_absolute_worker_path(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            env = self.fixture(root)
            runner = root / 'systemd-run'
            runner.write_text('#!/bin/sh\nprintf "%s\\n" "$@" > "$FIXTURE_LOG"\n', encoding='utf-8')
            runner.chmod(0o755)
            subprocess.run(['bash', './dusky_session.sh', 'reboot'], cwd=SCRIPT.parent,
                           env=env, check=True, capture_output=True, timeout=5)
            arguments = (root / 'calls').read_text().splitlines()
            self.assertEqual(Path(arguments[arguments.index('--') + 2]), SCRIPT)

    def test_rofi_and_wlogout_use_the_shared_helper(self):
        rofi = SCRIPT.parents[1] / 'rofi/powermenu.sh'
        layout = SCRIPT.parents[2] / '.config/wlogout/layout'
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            helper = root / 'user_scripts/wlogout/dusky_session.sh'
            helper.parent.mkdir(parents=True)
            helper.write_text('#!/bin/sh\nprintf "%s\\n" "$1" >> "$FIXTURE_LOG"\n', encoding='utf-8')
            helper.chmod(0o755)
            for key, action in (('reboot', 'reboot'), ('shutdown', 'poweroff'), ('soft_reboot', 'soft-reboot'), ('logout', 'logout')):
                env = os.environ | {'HOME': directory, 'XDG_RUNTIME_DIR': directory,
                                    'FIXTURE_LOG': str(root / 'calls'), 'ROFI_INFO': key + ':confirmed', 'ROFI_RETV': '1'}
                subprocess.run(['bash', str(rofi)], env=env, check=True, capture_output=True, timeout=5)
            self.assertEqual((root / 'calls').read_text().splitlines(), ['reboot', 'poweroff', 'soft-reboot', 'logout'])
        decoder = json.JSONDecoder()
        text = layout.read_text(encoding='utf-8').strip()
        entries = []
        while text:
            entry, length = decoder.raw_decode(text)
            entries.append(entry)
            text = text[length:].lstrip()
        for entry in entries:
            if entry['label'] in ('reboot', 'shutdown', 'soft-reboot', 'logout'):
                self.assertIn('$HOME/user_scripts/wlogout/dusky_session.sh ', entry['action'])


if __name__ == '__main__':
    unittest.main()
