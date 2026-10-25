"""Regressions found while launching the current IDD client on Windows 11."""

import configparser
from pathlib import Path
import runpy
import unittest


merge_config = runpy.run_path(
    str(Path(__file__).resolve().parents[1] / "60_configure_client_ini.py")
)["merge_config"]


class ClientConfigTests(unittest.TestCase):
    def parse(self, content):
        config = configparser.ConfigParser(strict=False)
        config.read_string(content)
        return config

    def test_legacy_resize_conflict_is_removed(self):
        result = self.parse(merge_config("[win]\nautoResize=yes\n", "/dev/shm/looking-glass"))
        self.assertFalse(result.getboolean("win", "autoResize"))
        self.assertTrue(result.getboolean("win", "setGuestRes"))

    def test_missing_sections_receive_working_defaults(self):
        result = self.parse(merge_config("", "/dev/shm/looking-glass"))
        self.assertFalse(result.getboolean("win", "autoResize"))
        self.assertTrue(result.getboolean("win", "setGuestRes"))
        self.assertTrue(result.getboolean("spice", "enable"))

    def test_comments_and_unmanaged_options_survive(self):
        source = "; operator comment\n[wayland]\nfractionScale=no\n[win]\nkeepAspect=no\n"
        result = merge_config(source, "/dev/shm/looking-glass")
        self.assertIn("; operator comment", result)
        config = self.parse(result)
        self.assertFalse(config.getboolean("wayland", "fractionScale"))
        self.assertFalse(config.getboolean("win", "keepAspect"))

    def test_duplicate_managed_keys_do_not_restore_conflict(self):
        source = "[win]\nautoResize=yes\nsetGuestRes=no\n[win]\nautoResize=yes\n"
        result = merge_config(source, "/dev/shm/looking-glass")
        self.assertEqual(result.count("autoResize="), 1)
        self.assertFalse(self.parse(result).getboolean("win", "autoResize"))

    def test_migration_is_idempotent_and_updates_shm_alias(self):
        source = "[app]\nshmFile=/dev/shm/old\n[lgmp]\nshmDevice=/dev/shm/old\n"
        result = merge_config(source, "/dev/shm/looking-glass")
        self.assertEqual(merge_config(result, "/dev/shm/looking-glass"), result)
        config = self.parse(result)
        self.assertFalse(config.has_option("app", "shmFile"))
        self.assertEqual(config.get("lgmp", "shmDevice"), "/dev/shm/looking-glass")


if __name__ == "__main__":
    unittest.main()
