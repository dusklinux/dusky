"""Factory adapter checks without compiling the applications again."""

import importlib.util
import json
import os
import signal
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.dont_write_bytecode = True
BASE = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("factory_adapter_tests", BASE / "dusky-snap/build_rust.py")
adapter = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(adapter)


class AdapterTests(unittest.TestCase):
    def test_flags_jobs_paths_and_staged_binary(self):
        with tempfile.TemporaryDirectory() as root:
            root = Path(root)
            workspace = adapter.shared.Workspace(root / "scratch", True, 3)
            output = root / "output/example"
            calls = []
            def run(command, project, env, *, timeout):
                calls.append(command)
                self.assertNotIn("CARGO_ENCODED_RUSTFLAGS", env)
                self.assertEqual(env["RUSTFLAGS"], "-C target-cpu=x86-64")
                self.assertEqual(env["CFLAGS"], "-march=x86-64 -mtune=generic -O2")
                self.assertEqual(env["CXXFLAGS"], env["CFLAGS"])
                self.assertEqual(env["CARGO_TARGET_DIR"], str(workspace.path / "target"))
                self.assertEqual(env["CARGO_BUILD_BUILD_DIR"], env["CARGO_TARGET_DIR"])
                self.assertEqual(env["TMPDIR"], str(workspace.path))
                if command[1] == "build":
                    self.assertIn("--frozen", command)
                    self.assertEqual(command[command.index("--jobs") + 1], "3")
                    self.assertEqual(env["CARGO_NET_OFFLINE"], "true")
                    built = workspace.path / "target" / adapter.TARGET / "release/example"
                    built.parent.mkdir(parents=True)
                    built.write_bytes(b"compiled executable")
                    built.chmod(0o755)
            with (
                patch.object(adapter, "run", side_effect=run),
                patch.object(adapter.shutil, "which", return_value="/fixture/cargo"),
                patch.dict(os.environ, CARGO_ENCODED_RUSTFLAGS="invalid", CARGO_BUILD_JOBS="999"),
            ):
                adapter.build.__wrapped__(root, output, workspace=workspace)
            self.assertEqual(output.read_bytes(), b"compiled executable")
            self.assertEqual(output.stat().st_mode & 0o777, 0o755)
            self.assertEqual([command[1] for command in calls], ["fetch", "build"])

    def test_ram_build_exhaustion_requests_disk_retry(self):
        error = subprocess.CalledProcessError(101, ["cargo"], output="No space left on device")
        with tempfile.TemporaryDirectory() as root:
            root = Path(root)
            with patch.object(adapter, "run", side_effect=[None, error]):
                with self.assertRaises(adapter.shared.RetryOnDisk):
                    adapter.build.__wrapped__(root, root / "output", workspace=adapter.shared.Workspace(root, True, 1))

    def test_disk_failure_and_fetch_failure_are_not_retried(self):
        error = subprocess.CalledProcessError(101, ["cargo"], output="No space left on device")
        with tempfile.TemporaryDirectory() as root:
            root = Path(root)
            for ram, failures in ((False, [None, error]), (True, [error])):
                with self.subTest(ram=ram, phase="build" if len(failures) == 2 else "fetch"):
                    with patch.object(adapter, "run", side_effect=failures):
                        with self.assertRaises(subprocess.CalledProcessError):
                            adapter.build.__wrapped__(root, root / "output", workspace=adapter.shared.Workspace(root, ram, 1))

    def test_output_disk_exhaustion_does_not_recompile(self):
        with tempfile.TemporaryDirectory() as root:
            root = Path(root)
            with (
                patch.object(adapter, "run"),
                patch.object(adapter.shutil, "copy2", side_effect=OSError(28, "output disk full")),
            ):
                with self.assertRaisesRegex(RuntimeError, "Could not stage"):
                    adapter.build.__wrapped__(root, root / "output", workspace=adapter.shared.Workspace(root, True, 1))

    def test_killed_leader_cleans_compiler_descendants(self):
        with tempfile.TemporaryDirectory() as root:
            root = Path(root)
            metadata = root / "processes.json"
            code = (
                "import os,signal,subprocess,sys,json; "
                "child=subprocess.Popen([sys.executable,'-c','import time; time.sleep(30)'],"
                "stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL); "
                f"open({str(metadata)!r},'w').write(json.dumps({{'group':os.getpid(),'child':child.pid}})); "
                "os.kill(os.getpid(),signal.SIGKILL)"
            )
            try:
                with self.assertRaises(subprocess.CalledProcessError) as error:
                    adapter.run([sys.executable, "-c", code], root, os.environ.copy(), timeout=5)
                self.assertEqual(error.exception.returncode, -signal.SIGKILL)
                pid = json.loads(metadata.read_text())["child"]
                status = Path(f"/proc/{pid}/status")
                if status.exists():
                    self.assertIn("State:\tZ", status.read_text())
            finally:
                if metadata.exists():
                    try:
                        os.killpg(json.loads(metadata.read_text())["group"], signal.SIGKILL)
                    except ProcessLookupError:
                        pass

    def test_timeout_reaps_process(self):
        with tempfile.TemporaryDirectory() as root:
            root = Path(root)
            metadata = root / "pid"
            code = f"import os,time; open({str(metadata)!r},'w').write(str(os.getpid())); time.sleep(30)"
            with self.assertRaises(subprocess.TimeoutExpired):
                adapter.run([sys.executable, "-c", code], root, os.environ.copy(), timeout=0.2)
            self.assertFalse(Path(f"/proc/{metadata.read_text()}").exists())


if __name__ == "__main__":
    unittest.main(verbosity=2)
