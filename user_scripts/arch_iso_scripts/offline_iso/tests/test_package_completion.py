"""Check package-stage outcomes with stub transactions and temporary files."""
import os
from pathlib import Path
import shlex
import subprocess
import tempfile
import unittest

SCRIPT = Path(__file__).resolve().parents[1] / '130_chroot_package_installer.sh'


class PackageCompletion(unittest.TestCase):
    def run_installer(self, directory, **settings):
        root = Path(directory)
        keyring = root / 'keyring'
        keyring.mkdir()
        for name in ('trustdb.gpg', 'pubring.gpg'):
            (keyring / name).write_text('fixture')
        pacman = root / 'pacman'
        pacman.write_text('''#!/usr/bin/bash
printf '%s\\n' "$*" >> "$FIXTURE_CALLS"
[[ ${FIXTURE_INTERRUPT:-0} == 0 ]] || exit 130
for argument in "$@"; do
    if [[ $argument == fixture-missing && ${FIXTURE_FAIL:-0} == 1 ]]; then
        printf 'error: target not found: fixture-missing\\n' >&2
        exit 1
    fi
done
''')
        pacman.chmod(0o755)
        pacman_key = root / 'pacman-key'
        pacman_key.write_text('#!/bin/sh\nexit 0\n')
        pacman_key.chmod(0o755)

        source = SCRIPT.read_text()
        listing = source[source.index('list_package_targets() {'):source.index('\nif [[ $# == 1')]
        # Exercise the actual installer functions/main, using two fixture groups
        # and skipping only the privilege re-exec and real package configuration.
        body = source.split('# --- 3. SAFETY & ARGUMENT PARSING ---', 1)[1]
        body = body.replace("readonly SCRIPT_LOCK_FILE='/run/lock/elite-system-installer.lock'",
                            'readonly SCRIPT_LOCK_FILE=' + shlex.quote(str(root / 'lock')))
        body = body.replace("local keyring_dir='/etc/pacman.d/gnupg'",
                            'local keyring_dir=' + shlex.quote(str(keyring)))
        configuration = '''
declare -ar fixture_first=(fixture-missing)
declare -ar fixture_last=(fixture-later)
declare -ar GROUP_LABELS=('First group' 'Later group')
declare -ar GROUP_ARRAYS=(fixture_first fixture_last)
'''
        environment = os.environ | {'PATH': str(root) + ':' + os.environ['PATH'],
                                    'FIXTURE_CALLS': str(root / 'calls')} | settings
        result = subprocess.run(['bash', '-c', configuration + listing + body],
                                env=environment, capture_output=True, text=True, timeout=10)
        return result, (root / 'calls').read_text().splitlines()

    def test_complete_transaction_reports_success(self):
        with tempfile.TemporaryDirectory() as directory:
            result, calls = self.run_installer(directory)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertEqual(len(calls), 1)
            self.assertIn(':: INSTALLATION COMPLETE ::', result.stdout)

    def test_missing_package_is_reported_without_blocking_later_setup(self):
        with tempfile.TemporaryDirectory() as directory:
            result, calls = self.run_installer(directory, FIXTURE_FAIL='1')
            self.assertIn('--sync --needed --noconfirm -- fixture-later', calls)
            self.assertIn('First group :: fixture-missing', result.stdout)
            self.assertIn('Failed/Skipped packages: 1', result.stdout)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_interrupted_transaction_preserves_its_exit_status(self):
        with tempfile.TemporaryDirectory() as directory:
            result, calls = self.run_installer(directory, FIXTURE_INTERRUPT='1')
            self.assertEqual(result.returncode, 130, result.stdout + result.stderr)
            self.assertEqual(len(calls), 1)


if __name__ == '__main__':
    unittest.main()
