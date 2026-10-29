"""Exercise the real pure serializer without modifying PAM or starting a greeter."""
import ast
import json
import os
from pathlib import Path
import re
import subprocess
import tempfile
import tomllib
import unittest

SCRIPT = Path(__file__).resolve().parents[1] / 'scripts/127_pam_keyring_greetd.py'


class GreetdConfig(unittest.TestCase):
    def render(self, directory, mode='password', previous=None):
        path = Path(directory) / 'config.toml'
        if previous is not None:
            path.write_text(previous)
        namespace = {'json': json, 're': re, 'tomllib': tomllib, 'GREETD_CFG': path,
                     'WAYLAND_SESSION': Path('/usr/local/bin/wayland-session')}
        tree = ast.parse(SCRIPT.read_text())
        function = next(node for node in tree.body if isinstance(node, ast.FunctionDef)
                        and node.name == 'greetd_content')
        exec(compile(ast.Module(body=[function], type_ignores=[]), str(SCRIPT), 'exec'), namespace)
        return namespace['greetd_content']('fixture-user', mode)

    def test_password_login_emits_the_bare_keys_consumed_by_greetd(self):
        with tempfile.TemporaryDirectory() as directory:
            content = self.render(directory)
            parsed = tomllib.loads(content)
            self.assertEqual(parsed['default_session']['user'], 'greeter')
            self.assertNotIn('initial_session', parsed)
            self.assertIn('\ncommand = "tuigreet ', content)
            self.assertNotRegex(content, r'(?m)^"[^"\n]+"\s*=')

    def test_explicit_autologin_and_luks_keep_the_requested_account(self):
        for mode in ('autologin', 'luks'):
            with self.subTest(mode=mode), tempfile.TemporaryDirectory() as directory:
                parsed = tomllib.loads(self.render(directory, mode))
                self.assertEqual(parsed['initial_session'],
                                 {'command': '/usr/local/bin/wayland-session', 'user': 'fixture-user'})

    def test_existing_terminal_and_general_values_are_preserved(self):
        previous = ('[terminal]\nvt=9\nswitch=false\n[general]\n'
                    'source_profile=false\nrunfile="/run/custom-greetd"\n')
        with tempfile.TemporaryDirectory() as directory:
            parsed = tomllib.loads(self.render(directory, previous=previous))
            self.assertEqual(parsed['terminal'], {'vt': 1, 'switch': False})
            self.assertEqual(parsed['general'], {'source_profile': False, 'runfile': '/run/custom-greetd'})

    def test_setting_names_outside_greetds_bare_key_syntax_are_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaises(ValueError):
                self.render(directory, previous='[general]\n"unsupported key"=true\n')

    def test_wayland_launcher_routes_output_to_the_journal_and_preserves_failure(self):
        tree = ast.parse(SCRIPT.read_text())
        assignment = next(node for node in tree.body if isinstance(node, ast.Assign)
                          and any(isinstance(target, ast.Name) and target.id == 'SESSION_CONTENT'
                                  for target in node.targets))
        launcher = ast.literal_eval(assignment.value)
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for name in ('systemd-cat', 'start-hyprland'):
                command = root / name
                command.write_text('#!/bin/sh\nprintf "%s\\n" "$0" "$@" > "$FIXTURE_LOG"\nexit "$FIXTURE_RC"\n')
                command.chmod(0o755)
            for status in (0, 7):
                environment = os.environ | {'PATH': directory + ':' + os.environ['PATH'],
                                            'FIXTURE_LOG': str(root / 'calls'), 'FIXTURE_RC': str(status)}
                result = subprocess.run(['bash', '-c', launcher], env=environment, capture_output=True)
                self.assertEqual(result.returncode, status)
                arguments = (root / 'calls').read_text().splitlines()
                self.assertEqual(Path(arguments[0]).name, 'systemd-cat')
                self.assertEqual(arguments[1:], ['--identifier=hyprland-session',
                                                 '--stderr-priority=err', 'start-hyprland'])


if __name__ == '__main__':
    unittest.main()
