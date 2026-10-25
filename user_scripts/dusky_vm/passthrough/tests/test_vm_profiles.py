"""Exercise the profile runner using real child processes and isolated RAM files."""

import importlib.util
import json
import os
import pty
import select
import signal
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

ENTRY = Path(__file__).resolve().parents[1] / "000_dusky_vm.py"
spec = importlib.util.spec_from_file_location("vm_profiles", ENTRY)
runner = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = runner
spec.loader.exec_module(runner)


class VMProfileTests(unittest.TestCase):
    def setUp(self):
        work = tempfile.TemporaryDirectory(prefix="vm-profiles-")
        self.addCleanup(work.cleanup)
        self.root = Path(work.name)
        self.events = self.root / "events.jsonl"
        self.state = self.root / "state"
        self.runtime = self.root / "runtime"
        self.env = os.environ | {"XDG_RUNTIME_DIR": str(self.runtime),
                                 "EVENTS": str(self.events), "PYTHONDONTWRITEBYTECODE": "1"}
        self.setup = self.script("setup task.py")
        self.deploy = self.script("deploy.py")
        self.profile = self.make_profile([
            {"script": str(self.setup)},
            {"script": str(self.deploy), "always": True, "forward_args": True},
        ])

    def script(self, name, source=None):
        path = self.root / name
        path.write_text(source or '''import json,os,sys
from pathlib import Path
with Path(os.environ['EVENTS']).open('a',encoding='utf-8') as stream:
    stream.write(json.dumps([Path(__file__).name,sys.argv[1:]])+'\\n')
if os.environ.get('FAIL_TASK')==Path(__file__).name:sys.exit(37)
''', encoding="utf-8")
        return path

    def make_profile(self, tasks, name="fixture.toml"):
        path = self.root / name
        text = '[profile]\nname="Test VM"\n'
        for task in tasks:
            text += '\n[[sequence.tasks]]\n'
            for key, value in task.items():
                text += f"{key} = {json.dumps(value)}\n"
        path.write_text(text, encoding="utf-8")
        return path

    def command(self, *args):
        return [sys.executable, "-B", str(ENTRY), "--profile", str(self.profile),
                "--state-dir", str(self.state), *args]

    def run_cli(self, *args, env=None):
        return subprocess.run(self.command(*args), cwd=self.root, env=env or self.env,
                              capture_output=True, text=True, timeout=10)

    def recorded(self):
        return [json.loads(line) for line in self.events.read_text().splitlines()]

    def test_dry_run_does_not_write_or_execute(self):
        before = set(self.root.rglob("*"))
        result = self.run_cli("--dry-run", "--", "--name", "VM with spaces")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(set(self.root.rglob("*")), before)

    def test_setup_resumes_but_deployment_runs_each_time_and_preserves_argv(self):
        extra = ["--name", "spaces λ $(not-a-command)", "--iso", "/tmp/dusky test.iso"]
        for _ in range(2):
            result = self.run_cli("--", *extra)
            self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.recorded(), [[self.setup.name, []],
                                          [self.deploy.name, extra], [self.deploy.name, extra]])
        self.assertEqual(len(list(self.state.rglob("*.json"))), 1)
        self.assertEqual(len(list(self.runtime.rglob("*.json"))), 2)

    def test_changed_script_invalidates_its_receipt(self):
        self.assertEqual(self.run_cli().returncode, 0)
        self.setup.write_text(self.setup.read_text() + "\n# updated implementation\n")
        self.assertEqual(self.run_cli().returncode, 0)
        self.assertEqual([r[0] for r in self.recorded()].count(self.setup.name), 2)

    def test_concurrent_runs_do_not_take_a_runner_lock_or_damage_receipts(self):
        self.setup.write_text(self.setup.read_text() + "\nimport time; time.sleep(.3)\n")
        processes = [subprocess.Popen(self.command(), env=self.env, stdout=subprocess.PIPE,
                                      stderr=subprocess.PIPE) for _ in range(2)]
        for proc in processes:
            output, error = proc.communicate(timeout=10)
            self.assertEqual(proc.returncode, 0, (output, error))
        self.assertEqual([r[0] for r in self.recorded()].count(self.deploy.name), 2)
        receipts = list(self.state.rglob("*.json"))
        self.assertEqual(len(receipts), 1)
        self.assertIn("completed", json.loads(receipts[0].read_text()))
        self.assertEqual(len(list(self.runtime.rglob("*.json"))), 2)

    def test_failed_task_stops_and_successful_prefix_is_resumed(self):
        failing = self.script("failure.py")
        self.profile = self.make_profile([{"script": str(self.setup)}, {"script": str(failing)},
            {"script": str(self.deploy), "always": True, "forward_args": True}])
        result = self.run_cli(env=self.env | {"FAIL_TASK": failing.name})
        self.assertEqual(result.returncode, 37)
        self.assertEqual([r[0] for r in self.recorded()], [self.setup.name, failing.name])
        self.assertEqual(len(list(self.state.rglob("*.json"))), 1)
        self.assertEqual(self.run_cli().returncode, 0)
        self.assertEqual([r[0] for r in self.recorded()],
                         [self.setup.name, failing.name, failing.name, self.deploy.name])

    def test_missing_later_script_fails_before_any_execution(self):
        self.profile = self.make_profile([{"script": str(self.setup)},
                                          {"script": str(self.root / "missing.py")}])
        self.assertEqual(self.run_cli().returncode, 1)
        self.assertFalse(self.events.exists())
        self.assertFalse(self.state.exists())
        self.assertFalse(self.runtime.exists())

    def test_reset_and_phase_selection(self):
        self.assertEqual(self.run_cli("--setup-only").returncode, 0)
        self.assertEqual(self.run_cli("--deploy-only").returncode, 0)
        self.assertEqual(self.run_cli("--reset").returncode, 0)
        self.assertEqual(len(list(self.state.rglob("*.json"))), 0)
        self.assertEqual(self.run_cli("--setup-only").returncode, 0)
        self.assertEqual([r[0] for r in self.recorded()],
                         [self.setup.name, self.deploy.name, self.setup.name])
        self.assertEqual(self.run_cli("--setup-only", "--", "--name", "oops").returncode, 1)

    def test_duplicate_tasks_have_separate_completion_receipts(self):
        self.profile = self.make_profile([{"script": str(self.setup)}, {"script": str(self.setup)}])
        self.assertEqual(self.run_cli().returncode, 0)
        self.assertEqual(self.run_cli().returncode, 0)
        self.assertEqual(len(self.recorded()), 2)
        self.assertEqual(len(list(self.state.rglob("*.json"))), 2)

    def test_profile_selection_and_real_plans(self):
        paths = sorted(runner.PROFILES_DIR.glob("*.toml"))
        for query in ("1", "linux", "Arch Linux", "01_arch_linux", "01_arch_linux.toml"):
            self.assertEqual(runner.select_profile(paths, query), paths[0])
        with self.assertRaisesRegex(ValueError, "found 2"):
            runner.select_profile(paths, "0")
        linux, windows = [runner.load_profile(path) for path in paths]
        self.assertNotIn("15_gpu_probing_kernal_param_mkinit.py", [t.script.name for t in linux.tasks])
        self.assertIn("--nat", linux.tasks[-2].args)
        self.assertEqual(windows.tasks[-1].args, ("--windows",))
        self.assertTrue(all(p.tasks[-1].always and p.tasks[-1].forward_args for p in (linux, windows)))

    def test_invalid_boolean_or_unknown_field_is_not_silently_accepted(self):
        for fields in ({"always": "true"}, {"always_run": True}, {"forward_args": True}):
            self.profile = self.make_profile([{"script": str(self.setup)} | fields])
            self.assertEqual(self.run_cli().returncode, 1)
        self.assertFalse(self.events.exists())

    def test_windows_dispatch_flag_survives_sudo_reexec(self):
        fakebin = self.root / "bin"
        fakebin.mkdir()
        sudo = fakebin / "sudo"
        sudo.write_text(f"#!{sys.executable}\nimport json,sys\nprint(json.dumps(sys.argv[1:]))\n")
        sudo.chmod(0o755)
        result = subprocess.run([sys.executable, str(ENTRY.with_name("30_kvm_vm_deploy.py")),
                                 "--windows", "--force-disk"], capture_output=True, text=True,
                                env=self.env | {"PATH": str(fakebin) + os.pathsep + os.environ["PATH"]}, timeout=5)
        self.assertEqual(result.returncode, 0, result.stderr)
        forwarded = json.loads(result.stdout)
        self.assertIn(str(ENTRY.parent / "windows/deploy_vm.py"), forwarded)
        self.assertIn("--force-disk", forwarded)

    def test_sigterm_stops_child_group_and_does_not_mark_completed(self):
        marker = self.root / "pid"
        sleeper = self.script("sleep.py", f"import os,time\nfrom pathlib import Path\nPath({str(marker)!r}).write_text(str(os.getpid()))\ntime.sleep(30)\n")
        self.profile = self.make_profile([{"script": str(sleeper)}])
        proc = subprocess.Popen(self.command(), env=self.env, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        self.addCleanup(lambda: proc.kill() if proc.poll() is None else None)
        deadline = time.monotonic() + 5
        while not marker.exists() and time.monotonic() < deadline:
            time.sleep(.01)
        self.assertTrue(marker.exists())
        proc.send_signal(signal.SIGTERM)
        proc.communicate(timeout=5)
        self.assertEqual(proc.returncode, 143)
        self.assertFalse(Path(f"/proc/{marker.read_text()}").exists())
        self.assertFalse(list(self.state.rglob("*.json")))

    def test_interactive_child_owns_terminal_and_runner_restores_it(self):
        prompt = self.script("prompt.py", '''import os,sys
print('FOREGROUND',sys.stdin.isatty(),os.tcgetpgrp(0)==os.getpgrp(),flush=True)
print('ACCEPTED',input('fixture input: '),flush=True)
''')
        self.profile = self.make_profile([{"script": str(prompt), "always": True}])
        wrapper = "import os,runpy,sys; m=runpy.run_path(sys.argv[1]); result=m['main'](sys.argv[2:]); print('RESTORED',os.tcgetpgrp(0)==os.getpgrp(),flush=True); sys.exit(result)"
        pid, fd = pty.fork()
        if pid == 0:
            os.execve(sys.executable, [sys.executable, "-c", wrapper, str(ENTRY),
                                      *self.command()[3:]], self.env)
        output = bytearray()
        sent = False
        status = None
        try:
            deadline = time.monotonic() + 8
            while time.monotonic() < deadline:
                if select.select([fd], [], [], .05)[0]:
                    try:
                        output.extend(os.read(fd, 65536))
                    except OSError:
                        pass
                if b"fixture input:" in output and not sent:
                    os.write(fd, b"hello\n")
                    sent = True
                ended, status = os.waitpid(pid, os.WNOHANG)
                if ended:
                    break
            else:
                os.kill(pid, signal.SIGKILL)
                os.waitpid(pid, 0)
                self.fail(f"Interactive task stalled: {output!r}")
            self.assertEqual(os.waitstatus_to_exitcode(status), 0, output)
            self.assertIn(b"FOREGROUND True True", output)
            self.assertIn(b"ACCEPTED hello", output)
            self.assertIn(b"RESTORED True", output)
        finally:
            os.close(fd)


if __name__ == "__main__":
    unittest.main()
