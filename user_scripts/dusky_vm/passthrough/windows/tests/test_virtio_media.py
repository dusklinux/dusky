"""Exercise media staging without importing Phase 1's privileged startup."""

import ast
import argparse
from contextlib import nullcontext
import os
from pathlib import Path
import pwd
import shutil
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch


source_path = Path(__file__).resolve().parents[2] / "05_virtio_iso.py"
tree = ast.parse(source_path.read_text(encoding="utf-8"))
stage_node = next(node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name == "stage_virtio")
stage_code = compile(ast.Module(body=[stage_node], type_ignores=[]), str(source_path), "exec")
main_node = next(node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name == "main")
main_code = compile(ast.Module(body=[main_node], type_ignores=[]), str(source_path), "exec")


class VirtioMediaTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.pool = self.root / "pool"
        self.pool.mkdir()
        self.namespace = {
            "Path": Path, "pwd": pwd, "os": os, "shutil": shutil,
            "console": Mock(), "aur_virtio_iso": Mock(return_value=None),
            "install_aur": Mock(side_effect=AssertionError("Unexpected AUR request")),
            "download_iso": Mock(side_effect=AssertionError("Unexpected download")),
            "human": str, "VIRTIO_URL": "unused",
            "PathCompleter": lambda home: nullcontext(SimpleNamespace(expand=lambda value: value)),
        }
        exec(stage_code, self.namespace)

    def make_iso(self, path):
        with path.open("wb") as handle:
            handle.truncate(80_000_000)
        return path

    def stage(self):
        self.namespace["stage_virtio"](SimpleNamespace(pw_dir=str(self.root)), self.pool, 1)

    def test_staged_iso_requires_neither_package_lookup_nor_network(self):
        target = self.make_iso(self.pool / "virtio-win.iso")
        inode = target.stat().st_ino
        self.stage()
        self.namespace["aur_virtio_iso"].assert_not_called()
        self.assertEqual(target.stat().st_ino, inode)

    def test_installed_package_iso_is_used_without_aur_install(self):
        media = self.make_iso(self.root / "packaged.iso")
        self.namespace["aur_virtio_iso"].return_value = media
        self.stage()
        self.assertEqual((self.pool / "virtio-win.iso").resolve(), media)
        self.namespace["install_aur"].assert_not_called()

    def test_user_supplied_iso_is_copied_without_network(self):
        media = self.make_iso(self.root / "local media.iso")
        with patch("builtins.input", return_value=str(media)):
            self.stage()
        self.assertEqual((self.pool / "virtio-win.iso").stat().st_size, media.stat().st_size)
        self.namespace["install_aur"].assert_not_called()
        self.namespace["download_iso"].assert_not_called()

    def test_complete_offline_package_set_does_not_expire_with_sync_database(self):
        class ReachedOperator(Exception):
            pass
        namespace = dict(self.namespace, argparse=argparse, Panel=Mock(),
                         REPO_PACKAGES=["qemu-desktop", "libvirt"],
                         installed=Mock(return_value=True),
                         pacman_db_is_fresh=Mock(return_value=False),
                         resolve_operator=Mock(side_effect=ReachedOperator),
                         bail=Mock(side_effect=AssertionError("Unexpected package failure")))
        exec(main_code, namespace)
        with patch.object(sys, "argv", [str(source_path), "--skip-virtio"]):
            with self.assertRaises(ReachedOperator):
                namespace["main"]()
        namespace["pacman_db_is_fresh"].assert_not_called()

    def test_missing_packages_still_require_a_consistent_sync_database(self):
        namespace = dict(self.namespace, argparse=argparse, Panel=Mock(),
                         REPO_PACKAGES=["qemu-desktop"], installed=Mock(return_value=False),
                         pacman_db_is_fresh=Mock(return_value=False),
                         resolve_operator=Mock(), bail=Mock(side_effect=RuntimeError("stale database")))
        exec(main_code, namespace)
        with patch.object(sys, "argv", [str(source_path), "--skip-virtio"]):
            with self.assertRaisesRegex(RuntimeError, "stale database"):
                namespace["main"]()
        namespace["resolve_operator"].assert_not_called()


if __name__ == "__main__":
    unittest.main()
