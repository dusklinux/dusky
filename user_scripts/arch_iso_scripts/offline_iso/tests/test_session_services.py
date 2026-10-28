"""Exercise service detection and logout ordering without touching the live session."""
import os
from pathlib import Path
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
HOME = ROOT.parents[2]


class SessionServices(unittest.TestCase):
    def test_chroot_unit_detection(self):
        source = (ROOT / '170_services.sh').read_text().rsplit('\nmain', 1)[0]
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            units = root / 'usr/lib/systemd/system'
            units.mkdir(parents=True)
            (units / 'present.service').write_text('[Service]\nExecStart=/bin/true\n')
            source = source.replace('--root=/', '--root=' + directory)
            result = subprocess.run(['bash', '-c', source + '\nunit_exists present.service && ! unit_exists absent.service'],
                                    env=os.environ | {'SYSTEMD_IN_CHROOT': '1'}, capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_enable_failure_is_propagated(self):
        source = (ROOT / '170_services.sh').read_text().rsplit('\nmain', 1)[0]
        source += '\nconfigure_ssh() { :; }; configure_io_schedulers() { :; }; unit_exists() { return 0; }; systemctl() { return 1; }; main\n'
        result = subprocess.run(['bash', '-c', source], capture_output=True, text=True)
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)

    def test_logout_stops_services_before_compositor(self):
        for failure in (False, True):
            with tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                log = root / 'calls'
                for name, content in {
                    'ps': '#!/bin/sh\nprintf "1\\n"\n',
                    'hyprctl': '''#!/bin/sh
if [ "$1" = -j ]; then printf '[]\\n'; exit 0; fi
if [ "$2" = 'hl.dsp.exit()' ]; then
    printf 'exit\\n' >> "$FIXTURE_LOG"
    if [ "$FIXTURE_FAIL" = 1 ]; then printf 'error\\n'; exit 1; fi
fi
printf 'ok\\n'
''',
                    'systemctl': '#!/bin/sh\nprintf "%s\\n" "$*" >> "$FIXTURE_LOG"\n',
                }.items():
                    file = root / name
                    file.write_text(content)
                    file.chmod(0o755)
                env = os.environ | {'PATH': directory + ':' + os.environ['PATH'],
                                    'FIXTURE_LOG': str(log), 'FIXTURE_FAIL': str(int(failure))}
                result = subprocess.run(['bash', str(HOME/'user_scripts/wlogout/dusky_session.sh'), '--worker', 'logout', str(os.getpid())],
                                        env=env, capture_output=True, text=True, timeout=5)
                calls = log.read_text().splitlines()
                self.assertEqual(calls[:3], ['--user is-active --quiet graphical-session.target', '--user stop graphical-session.target', 'exit'])
                self.assertEqual(result.returncode, int(failure), result.stdout + result.stderr)
                if failure:
                    self.assertEqual(calls[-1], '--user start hyprland-session.target')


if __name__ == '__main__':
    unittest.main()
