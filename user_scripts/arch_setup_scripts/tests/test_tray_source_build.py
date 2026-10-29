"""Ensure Tray fingerprints depend on tracked sources, not generated headers."""
import importlib.util
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]


class TraySourceBuild(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        spec = importlib.util.spec_from_file_location('tray_build_fixture',
                                                     ROOT / 'scripts/152_dusky_tray_setup.py')
        cls.module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(cls.module)

    def fixture(self, directory):
        project = Path(directory) / 'dusky_tray'
        (project / 'src').mkdir(parents=True)
        for name, content in (('Cargo.toml', '[package]\nname="fixture"\nversion="1.0.0"\n'),
                              ('Cargo.lock', '# fixture\n'), ('src/main.rs', 'fn main() {}\n')):
            (project / name).write_text(content)
        helper = project.parent / 'click_away_to_dismiss'
        helper.mkdir()
        (helper / 'dusky.c').write_text('/* fixture */\n')
        (helper / 'hyprland-focus-grab-v1.xml').write_text('<protocol name="fixture"/>\n')
        return project, helper

    def digest(self, project):
        with patch.object(self.module.build_support, 'font_source_files', return_value=[]):
            return self.module.source_digest(project)

    def test_clean_checkout_needs_no_generated_protocol_files(self):
        with tempfile.TemporaryDirectory() as directory:
            project, _ = self.fixture(directory)
            self.assertEqual(len(self.digest(project)), 64)

    def test_protocol_changes_invalidate_the_source_digest(self):
        with tempfile.TemporaryDirectory() as directory:
            project, helper = self.fixture(directory)
            before = self.digest(project)
            (helper / 'hyprland-focus-grab-v1.xml').write_text('<protocol name="updated"/>\n')
            self.assertNotEqual(self.digest(project), before)

    def test_stale_generated_files_do_not_affect_the_source_digest(self):
        with tempfile.TemporaryDirectory() as directory:
            project, helper = self.fixture(directory)
            before = self.digest(project)
            for suffix in ('c', 'h'):
                (helper / f'hyprland-focus-grab-v1-client-protocol.{suffix}').write_text('stale artifact')
            self.assertEqual(self.digest(project), before)


if __name__ == '__main__':
    unittest.main()
