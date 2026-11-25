"""Verify Dusky Casts source digests, tool preflight fallbacks, desktop dedup, and legacy cleanup."""

import importlib.util
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]


def load_module(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class CastsSourceBuildTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.shared = load_module(
            "casts_shared_fixture",
            ROOT / "scripts/147_rust_compiles_shared_NOT_STANDALONE.py",
        )
        cls.casts = load_module(
            "casts_setup_fixture",
            ROOT / "scripts/154_dusky_casts_setup.py",
        )

    def make_project(self, base: Path, folder_name: str = "dusky_casts") -> Path:
        project = base / folder_name
        for sub in ("src", "proto", "assets", "data"):
            (project / sub).mkdir(parents=True)
        files = {
            "Cargo.toml": '[package]\nname = "dusky_casts"\nversion = "0.3.0"\n',
            "Cargo.lock": "# fixture lock\n",
            "build.rs": "fn main() {}\n",
            "LICENSE.md": "MPL-2.0\n",
            "src/main.rs": "fn main() {}\n",
            "proto/api.proto": 'syntax = "proto3";\n',
            "proto/sync.proto": 'syntax = "proto3";\n',
            "proto/bootstrap.proto": 'syntax = "proto3";\n',
            "assets/dusky_silence.lua": "-- silence trim fixture\n",
            "data/dusky_casts.desktop": "[Desktop Entry]\nName=Dusky Casts\nExec=dusky_casts\n",
            "data/dusky_casts.svg": "<svg/>\n",
        }
        for rel, content in files.items():
            (project / rel).write_text(content, encoding="utf-8")
        return project

    def test_digest_is_identical_across_checkout_and_makepkg_srcdir(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            home_checkout = self.make_project(root / "user_scripts/dusky", "dusky_casts")
            pkg_srcdir = self.make_project(root / "makepkg/srcdir", "project")
            self.assertEqual(
                self.casts.source_digest(home_checkout),
                self.casts.source_digest(pkg_srcdir),
            )
            self.assertEqual(len(self.casts.source_digest(home_checkout)), 64)

    def test_digest_invalidates_on_proto_assets_data_and_src_changes(self):
        with tempfile.TemporaryDirectory() as tmp:
            project = self.make_project(Path(tmp))
            baseline = self.casts.source_digest(project)

            for rel in (
                "src/main.rs",
                "proto/api.proto",
                "proto/sync.proto",
                "proto/bootstrap.proto",
                "assets/dusky_silence.lua",
                "data/dusky_casts.desktop",
                "data/dusky_casts.svg",
            ):
                with self.subTest(changed=rel):
                    path = project / rel
                    original = path.read_text(encoding="utf-8")
                    try:
                        path.write_text(original + "\n# changed\n", encoding="utf-8")
                        self.assertNotEqual(self.casts.source_digest(project), baseline)
                    finally:
                        path.write_text(original, encoding="utf-8")
            self.assertEqual(self.casts.source_digest(project), baseline)

    def test_digest_ignores_unrelated_docs_and_build_artifacts(self):
        with tempfile.TemporaryDirectory() as tmp:
            project = self.make_project(Path(tmp))
            before = self.casts.source_digest(project)
            (project / "README.md").write_text("docs only\n", encoding="utf-8")
            (project / "target/debug").mkdir(parents=True)
            (project / "target/debug/dusky_casts").write_bytes(b"artifact")
            self.assertEqual(self.casts.source_digest(project), before)

    def test_shared_extra_files_inside_project_is_path_independent_and_rejects_escape(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            p1 = self.make_project(root / "a", "dusky_casts")
            p2 = self.make_project(root / "b", "project")
            d1 = self.shared.source_digest(
                p1,
                recipe="test-r",
                include_fonts=False,
                extra_files=(p1 / "proto/api.proto",),
            )
            d2 = self.shared.source_digest(
                p2,
                recipe="test-r",
                include_fonts=False,
                extra_files=(p2 / "proto/api.proto",),
            )
            self.assertEqual(d1, d2)

            for bad_dir in ("../outside", "/etc", ""):
                with self.subTest(bad_dir=bad_dir):
                    with self.assertRaises(ValueError):
                        self.shared.source_digest(
                            p1,
                            recipe="test-r",
                            include_fonts=False,
                            extra_dirs=(bad_dir,),
                        )

    def test_missing_protoc_falls_back_to_system_binary(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            home = root / "home"
            home.mkdir()
            project = self.make_project(root, "dusky_casts")
            install_dir = home / ".local/share/dusky/dusky_casts"
            install_dir.mkdir(parents=True)
            fake_sys_bin = root / "usr/bin/dusky_casts"
            fake_sys_bin.parent.mkdir(parents=True)
            fake_sys_bin.write_bytes(b"\x7fELF\x02\x01\x01" + b"\x00" * 11 + b"\x3e\x00")
            fake_sys_bin.chmod(0o755)
            fake_sys_digest = root / "usr/share/dusky_casts/source.sha256"
            fake_sys_digest.parent.mkdir(parents=True)
            fake_sys_digest.write_text("stale-digest\n", encoding="utf-8")

            def fake_which(name: str):
                return None if name == "protoc" else f"/usr/bin/{name}"

            with (
                patch.dict(os.environ, {"XDG_DATA_HOME": str(home / ".local/share")}, clear=False),
                patch.object(self.casts, "SYSTEM_BINARY", fake_sys_bin),
                patch.object(self.casts, "SYSTEM_SOURCE_DIGEST", fake_sys_digest),
                patch.object(self.casts, "SYSTEM_DESKTOP", root / "missing.desktop"),
                patch.object(self.casts, "SYSTEM_ICON", root / "missing.svg"),
                patch.object(self.casts, "binary_runs", side_effect=lambda p: p == fake_sys_bin),
                patch.object(self.casts, "binary_version", return_value="0.3.0"),
                patch.object(self.casts.shutil, "which", side_effect=fake_which),
            ):
                rc = self.casts.setup(project, install_dir, force=False, native=True, home=home)

            self.assertEqual(rc, 0)
            self.assertEqual((install_dir / "dusky_casts").readlink(), fake_sys_bin)
            self.assertEqual((home / ".local/bin/dusky_casts").readlink(), install_dir / "dusky_casts")

    def test_stale_legacy_cleanup_and_user_desktop_install_when_no_system_pkg(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            home = root / "home"
            project = self.make_project(root, "dusky_casts")

            stale_paths = [
                home / ".local/bin/duskcasts",
                home / ".local/share/applications/duskcasts.desktop",
                home / ".local/share/applications/io.github.dusklinux.DuskCasts.desktop",
                home / ".local/share/icons/hicolor/scalable/apps/io.github.dusklinux.DuskCasts.svg",
                home / ".local/share/licenses/duskcasts/LICENSE.md",
            ]
            for stale in stale_paths:
                stale.parent.mkdir(parents=True, exist_ok=True)
                stale.write_text("stale\n", encoding="utf-8")

            with (
                patch.dict(os.environ, {"XDG_DATA_HOME": str(home / ".local/share")}, clear=False),
                patch.object(self.casts, "SYSTEM_DESKTOP", root / "absent/dusky_casts.desktop"),
                patch.object(self.casts, "SYSTEM_ICON", root / "absent/dusky_casts.svg"),
                patch.object(self.casts.shutil, "which", return_value=None),
            ):
                self.casts.sync_desktop_and_cleanup(project, home)

            for stale in stale_paths:
                self.assertFalse(stale.exists(), f"Expected stale path removed: {stale}")

            self.assertTrue((home / ".local/share/applications/dusky_casts.desktop").is_file())
            self.assertTrue((home / ".local/share/icons/hicolor/scalable/apps/dusky_casts.svg").is_file())
            self.assertTrue((home / ".local/share/licenses/dusky_casts/LICENSE.md").is_file())

    def test_desktop_deduplicates_when_system_package_files_exist(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            home = root / "home"
            project = self.make_project(root, "dusky_casts")

            sys_desktop = root / "usr/share/applications/dusky_casts.desktop"
            sys_icon = root / "usr/share/icons/hicolor/scalable/apps/dusky_casts.svg"
            sys_desktop.parent.mkdir(parents=True)
            sys_icon.parent.mkdir(parents=True)
            sys_desktop.write_text("[Desktop Entry]\nName=Dusky Casts\n", encoding="utf-8")
            sys_icon.write_text("<svg/>\n", encoding="utf-8")

            user_desktop = home / ".local/share/applications/dusky_casts.desktop"
            user_desktop.parent.mkdir(parents=True)
            user_desktop.write_text("duplicate user desktop\n", encoding="utf-8")

            with (
                patch.dict(os.environ, {"XDG_DATA_HOME": str(home / ".local/share")}, clear=False),
                patch.object(self.casts, "SYSTEM_DESKTOP", sys_desktop),
                patch.object(self.casts, "SYSTEM_ICON", sys_icon),
                patch.object(self.casts.shutil, "which", return_value=None),
            ):
                self.casts.sync_desktop_and_cleanup(project, home)

            self.assertFalse(user_desktop.exists())

    def test_generic_flag_validation(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            project = self.make_project(root, "dusky_casts")
            with (
                patch.object(self.casts, "DEFAULT_PROJECT", project),
                patch.object(self.casts.platform, "machine", return_value="x86_64"),
            ):
                with self.assertRaises(SystemExit) as missing_out:
                    self.casts.main(["--generic"])
                self.assertEqual(missing_out.exception.code, 2)

                with self.assertRaises(SystemExit) as inside_src:
                    self.casts.main(["--generic", "--output-dir", str(project / "out")])
                self.assertEqual(inside_src.exception.code, 2)


if __name__ == "__main__":
    unittest.main(verbosity=2)
