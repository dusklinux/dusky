#!/usr/bin/env python3
"""Offline regressions: python3 -m unittest discover -s this_directory -v."""

import contextlib
import importlib.util
import io
import json
import sys
import tempfile
import threading
import time
import unittest
import zipfile
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(ROOT / "user_scripts/dusky_tui"))


def load_module(name: str, relative_path: str):
    spec = importlib.util.spec_from_file_location(name, ROOT / relative_path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


setup = load_module("dusky_setup_tests", "user_scripts/firefox/theme_matugen/dusky_sites_setup.py")
engine = load_module("dusky_engine_tests", "user_scripts/dusky_tui/python/engines/dusky_sites.py")
host = load_module("dusky_host_tests", ".config/firefox_extentions/dusky_sites/dusky_sites_host.py")


class DuskySitesTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.sites = self.root / "sites"
        self.sites.mkdir()
        self.config = self.root / "config.json"
        self.config.write_text(json.dumps({
            "webThemeEnabled": False,
            "forceUnthemedWebsites": False,
            "disabledSites": [],
            "websitesDir": str(self.sites),
        }), encoding="utf-8")

    def test_missing_palette_is_rejected_before_provisioning(self):
        template = self.root / ".config/dusky_sites/about.css"
        template.parent.mkdir(parents=True)
        template.write_text("/* fixture */", encoding="utf-8")
        for config in ({}, {"userChromeEnabled": False, "userContentEnabled": False},
                       {"colorsPath": str(self.root / "custom-missing.css")}):
            with self.subTest(config=config):
                config_file = self.root / ".config/dusky/settings/dusky_sites/config.json"
                config_file.parent.mkdir(parents=True, exist_ok=True)
                config_file.write_text(json.dumps(config), encoding="utf-8")
                with self.assertRaisesRegex(ValueError, "Matugen palette not found"):
                    setup.load_profile_styles(self.root)
                self.assertFalse((self.root / ".local/share/dusky-sites").exists())

    def test_existing_scoped_palette_is_accepted(self):
        template = self.root / ".config/dusky_sites/about.css"
        template.parent.mkdir(parents=True)
        template.write_text("/* fixture */", encoding="utf-8")
        palette = self.root / ".config/matugen/generated/dusky_sites.css"
        palette.parent.mkdir(parents=True)
        palette.write_text(setup.INTERNAL_DOCUMENT_RULE + " { :root {"
                           "--dusky-palette-background: #000000;"
                           "--dusky-palette-primary: #ffffff;"
                           "--dusky-palette-on_surface: #ffffff;"
                           "--dusky-palette-color-scheme: dark; }}", encoding="utf-8")
        self.assertEqual(setup.load_profile_styles(self.root),
                         (palette, "/* fixture */", True, True))

    def package_fixture(self, name="dusky_sites.xpi", *, matches=False, signed=True, extension_id=None):
        source = self.root / ".config/firefox_extentions/dusky_sites/extension"
        source.mkdir(parents=True, exist_ok=True)
        manifest = {"version": "6.2.2", "browser_specific_settings": {"gecko": {"id": setup.EXTENSION_ID}}}
        (source / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
        scripts = {name: name + " new source" for name in ("background.js", "content.js", "defaults.js")}
        for filename, text in scripts.items():
            (source / filename).write_text(text, encoding="utf-8")
        package = source.parent / "xpi" / name
        package.parent.mkdir(exist_ok=True)
        if not matches:
            manifest["version"] = "6.2.1"
        if extension_id is not None:
            manifest["browser_specific_settings"]["gecko"]["id"] = extension_id
        with zipfile.ZipFile(package, "w") as archive:
            archive.writestr("manifest.json", json.dumps(manifest))
            for filename, text in scripts.items():
                archive.writestr(filename, text if matches else "previous release")
            # Lookup metadata fixtures only; Firefox performs real signature validation.
            if signed:
                archive.writestr("META-INF/mozilla.rsa", "fixture")
                archive.writestr("META-INF/mozilla.sf", "fixture")
        return package

    def test_setup_errors_reach_tui_stderr_without_escape_codes(self):
        stdout, stderr = io.StringIO(), io.StringIO()
        with (contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr)):
            with self.assertRaises(SystemExit) as stopped:
                setup.print_error("fixture install failure")
        self.assertEqual(stopped.exception.code, 1)
        self.assertEqual(stdout.getvalue(), "")
        self.assertEqual(stderr.getvalue(), "Error: fixture install failure\n")

    def test_setup_can_select_existing_signed_release_while_source_changes(self):
        package = self.package_fixture()
        output = io.StringIO()
        with patch.object(Path, "home", return_value=self.root), contextlib.redirect_stdout(output):
            chosen = setup.resolve_source_xpi(self.root, require_source_match=False)
        self.assertEqual(chosen, package)
        self.assertIn("6.2.1", output.getvalue())
        self.assertIn("await signing", output.getvalue())

    def test_release_audit_still_requires_matching_sources(self):
        self.package_fixture()
        with patch.object(Path, "home", return_value=self.root):
            with self.assertRaisesRegex(ValueError, "does not match"):
                setup.resolve_source_xpi(self.root)

    def test_setup_prefers_matching_release_over_old_canonical_package(self):
        self.package_fixture()
        latest = self.package_fixture("latest-signed.xpi", matches=True)
        with patch.object(Path, "home", return_value=self.root):
            self.assertEqual(setup.resolve_source_xpi(self.root, require_source_match=False), latest)

    def test_setup_does_not_select_unsigned_development_package(self):
        self.package_fixture(signed=False)
        with patch.object(Path, "home", return_value=self.root):
            self.assertIsNone(setup.resolve_source_xpi(self.root, require_source_match=False))

    def test_setup_does_not_select_another_extension(self):
        self.package_fixture(extension_id="unrelated@example.test")
        with patch.object(Path, "home", return_value=self.root):
            self.assertIsNone(setup.resolve_source_xpi(self.root, require_source_match=False))

    def test_released_install_survives_missing_development_script(self):
        package = self.package_fixture()
        (package.parent.parent / "extension/defaults.js").unlink()
        with patch.object(Path, "home", return_value=self.root), contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(setup.resolve_source_xpi(self.root, require_source_match=False), package)
            with self.assertRaises(FileNotFoundError):
                setup.resolve_source_xpi(self.root)

    def test_released_install_survives_malformed_development_manifest(self):
        package = self.package_fixture()
        (package.parent.parent / "extension/manifest.json").write_text("{", encoding="utf-8")
        with patch.object(Path, "home", return_value=self.root), contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(setup.resolve_source_xpi(self.root, require_source_match=False), package)
            with self.assertRaises(ValueError):
                setup.resolve_source_xpi(self.root)

    def test_released_install_rejects_nonobject_development_manifest_for_audits(self):
        package = self.package_fixture()
        (package.parent.parent / "extension/manifest.json").write_text("[]", encoding="utf-8")
        with patch.object(Path, "home", return_value=self.root), contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(setup.resolve_source_xpi(self.root, require_source_match=False), package)
            with self.assertRaises(ValueError):
                setup.resolve_source_xpi(self.root)

    def test_incomplete_release_is_not_an_install_candidate(self):
        package = self.package_fixture()
        with zipfile.ZipFile(package) as archive:
            entries = {name: archive.read(name) for name in archive.namelist() if name != "content.js"}
        with zipfile.ZipFile(package, "w") as archive:
            for name, content in entries.items():
                archive.writestr(name, content)
        with patch.object(Path, "home", return_value=self.root), contextlib.redirect_stdout(io.StringIO()):
            self.assertIsNone(setup.resolve_source_xpi(self.root, require_source_match=False))

    def test_bad_utf8_package_does_not_hide_a_valid_release(self):
        broken = self.package_fixture()
        latest = self.package_fixture("latest-signed.xpi", matches=True)
        with zipfile.ZipFile(broken, "w") as archive:
            archive.writestr("manifest.json", b"\xff")
            for name in ("background.js", "content.js", "defaults.js", "META-INF/mozilla.rsa", "META-INF/mozilla.sf"):
                archive.writestr(name, "fixture")
        with patch.object(Path, "home", return_value=self.root), contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(setup.resolve_source_xpi(self.root, require_source_match=False), latest)

    def test_matching_release_reports_version_to_tui(self):
        package = self.package_fixture(matches=True)
        stdout = io.StringIO()
        with patch.object(Path, "home", return_value=self.root), contextlib.redirect_stdout(stdout):
            self.assertEqual(setup.resolve_source_xpi(self.root, require_source_match=False), package)
        self.assertIn("6.2.2", stdout.getvalue().splitlines()[0])
        self.assertNotIn("await signing", stdout.getvalue())

    def make_host(self):
        # Exercise protocol/configuration methods without opening stdin/inotify.
        instance = host.Host.__new__(host.Host)
        instance.cfg = host.Config()
        instance.cfg_sig = host.stat_sig(self.config)
        instance.retarget = Mock()
        instance.force = False
        instance.t_update = None
        instance.t_hard = None
        return instance

    def test_independent_engine_writers_preserve_both_edits(self):
        engines = [engine.DuskySitesEngine(str(self.config)) for _ in range(2)]
        start = threading.Barrier(2)
        for instance in engines:
            original = instance._read_config_json

            def delayed_read(*, strict=False, original=original):
                data = original(strict=strict)
                if strict:
                    time.sleep(0.05)  # Widen the read/modify/write race window.
                return data

            instance._read_config_json = delayed_read

        def write(index):
            start.wait(timeout=5)
            key = ("webThemeEnabled", "forceUnthemedWebsites")[index]
            return engines[index].write_value(key, "DEFAULT", "true", "bool")

        with ThreadPoolExecutor(2) as pool:
            outcomes = list(pool.map(write, range(2)))
        self.assertTrue(all(success for success, _, _ in outcomes), outcomes)
        data = json.loads(self.config.read_text(encoding="utf-8"))
        self.assertTrue(data["webThemeEnabled"])
        self.assertTrue(data["forceUnthemedWebsites"])

    def test_stale_host_patch_preserves_newer_tui_edit(self):
        instance = self.make_host()
        data = json.loads(self.config.read_text(encoding="utf-8"))
        data["webThemeEnabled"] = True
        self.config.write_text(json.dumps(data), encoding="utf-8")
        with patch.object(host, "CONFIG_PATH", self.config):
            instance.handle({"type": "SET_CONFIG", "config": {"debug": True}})
        saved = json.loads(self.config.read_text(encoding="utf-8"))
        self.assertTrue(saved["webThemeEnabled"])
        self.assertTrue(saved["debug"])
        self.assertTrue(instance.cfg.web_theme_enabled)

    def test_stale_host_same_value_patch_still_changes_disk(self):
        instance = self.make_host()
        self.config.write_text('{"webThemeEnabled": true}', encoding="utf-8")
        with patch.object(host, "CONFIG_PATH", self.config):
            instance.handle({"type": "SET_CONFIG", "config": {"webThemeEnabled": False}})
        self.assertFalse(json.loads(self.config.read_text(encoding="utf-8"))["webThemeEnabled"])

    def test_failed_host_commit_keeps_memory_and_disk(self):
        instance = self.make_host()
        original = self.config.read_bytes()
        with (patch.object(host, "CONFIG_PATH", self.config),
              patch.object(host, "atomic_write", side_effect=OSError("fixture disk failure")),
              contextlib.redirect_stderr(io.StringIO())):
            instance.handle({"type": "SET_CONFIG", "config": {"webThemeEnabled": True}})
        self.assertFalse(instance.cfg.web_theme_enabled)
        self.assertEqual(self.config.read_bytes(), original)

    def test_invalid_reload_keeps_last_good_settings(self):
        for invalid in ("{", "[]", '{"webThemeEnabled": "false"}', '{"disabledSites": null}', '{"colorsPath": "~__dusky_missing_test_user__/colors.css"}'):
            with self.subTest(invalid=invalid):
                instance = self.make_host()
                instance.cfg.web_theme_enabled = True
                self.config.write_text(invalid, encoding="utf-8")
                with (patch.object(host, "CONFIG_PATH", self.config),
                      contextlib.redirect_stderr(io.StringIO())):
                    self.assertFalse(instance.reload_config())
                self.assertTrue(instance.cfg.web_theme_enabled)

    def test_host_cannot_silently_rewrite_invalid_schema(self):
        instance = self.make_host()
        original = '{"disabledSites": null}'
        self.config.write_text(original, encoding="utf-8")
        with (patch.object(host, "CONFIG_PATH", self.config),
              contextlib.redirect_stderr(io.StringIO())):
            instance.handle({"type": "SET_CONFIG", "config": {"debug": True}})
        self.assertEqual(self.config.read_text(encoding="utf-8"), original)
        self.assertFalse(instance.cfg.debug)

    def test_nul_path_is_rejected_without_saving(self):
        self.config.write_text(json.dumps({"websitesDir": "/bad\0path"}), encoding="utf-8")
        instance = engine.DuskySitesEngine(str(self.config))
        original = self.config.read_bytes()
        self.assertFalse(instance.write_value("webThemeEnabled", "DEFAULT", "true", "bool")[0])
        self.assertEqual(self.config.read_bytes(), original)

    def test_unexpandable_user_path_is_rejected_without_saving(self):
        self.config.write_text('{"websitesDir": "~__dusky_missing_test_user__/sites"}', encoding="utf-8")
        instance = engine.DuskySitesEngine(str(self.config))
        original = self.config.read_bytes()
        self.assertFalse(instance.write_value("webThemeEnabled", "DEFAULT", "true", "bool")[0])
        self.assertEqual(self.config.read_bytes(), original)

    def test_remove_import_preserves_css_literals(self):
        path = self.root / "userChrome.css"
        directive = '@import url("dusky_about.css");'
        custom = f"/* example:\n{directive}\n*/\n.example::before {{content: '{directive}';}}\n"
        path.write_text(directive + "\n" + custom, encoding="utf-8")
        setup.remove_css_import(path, directive)
        self.assertEqual(path.read_text(encoding="utf-8"), custom)

    def test_quoted_comment_delimiters_do_not_hide_import(self):
        path = self.root / "userChrome.css"
        directive = '@import url("dusky_about.css");'
        path.write_text('p::before {content:"/*";}\n' + directive + '\np::after {content:"*/";}\n', encoding="utf-8")
        setup.ensure_css_import(path, directive)
        self.assertEqual(path.read_text(encoding="utf-8").count(directive), 1)

    def test_palette_alias_retarget_is_followed(self):
        first = self.root / "first.css"
        second = self.root / "second.css"
        first.write_text("first", encoding="utf-8")
        second.write_text("second", encoding="utf-8")
        alias = self.root / "palette.css"
        alias.symlink_to(first)
        link = self.root / "dusky_palette.css"
        setup.ensure_symlink(link, alias)
        alias.unlink()
        alias.symlink_to(second)
        self.assertEqual(link.read_text(encoding="utf-8"), "second")

    def test_multi_host_disabled_state_and_roundtrip(self):
        template = self.sites / "example.com.css"
        template.write_text('@-moz-document domain("example.com"), domain("other.example") {body{}}', encoding="utf-8")
        instance = engine.DuskySitesEngine(str(self.config))
        self.assertEqual(instance.load_state()["websitesDir"], str(self.sites))
        self.assertTrue(instance.write_value("site_example.com", "DEFAULT", "false", "bool")[0])
        self.assertFalse(instance.load_state()["site_example.com"])
        self.assertEqual(set(json.loads(self.config.read_text(encoding="utf-8"))["disabledSites"]), {"example.com", "other.example"})
        self.assertTrue(instance.write_value("site_example.com", "DEFAULT", "true", "bool")[0])
        self.assertEqual(json.loads(self.config.read_text(encoding="utf-8"))["disabledSites"], [])

    def test_invalid_batch_does_not_partially_save(self):
        instance = engine.DuskySitesEngine(str(self.config))
        original = self.config.read_bytes()
        outcome = instance.write_batch([
            ("webThemeEnabled", "DEFAULT", "true", "bool"),
            ("missing", "DEFAULT", "true", "bool"),
        ])
        self.assertFalse(outcome[0])
        self.assertEqual(self.config.read_bytes(), original)

    def test_failed_engine_publish_preserves_original(self):
        instance = engine.DuskySitesEngine(str(self.config))
        original = self.config.read_bytes()
        with patch.object(Path, "replace", side_effect=OSError("fixture publish failure")):
            self.assertFalse(instance.write_value("webThemeEnabled", "DEFAULT", "true", "bool")[0])
        self.assertEqual(self.config.read_bytes(), original)
        # The persistent lock is intentional; unpublished temporary data is not.
        self.assertEqual({path.name for path in self.root.iterdir()}, {"sites", "config.json", "config.json.lock"})

    def test_original_preferences_survive_repeated_setup_and_uninstall(self):
        path = self.root / "user.js"
        original = 'user_pref("extensions.enabledScopes", 2);\nuser_pref("custom.keep", true);\n'
        path.write_text(original, encoding="utf-8")
        for _ in range(5):
            self.assertTrue(setup.ensure_firefox_prefs(path))
        self.assertEqual(path.read_text(encoding="utf-8").count(setup.PREF_BLOCK_START), 1)
        with contextlib.redirect_stdout(io.StringIO()):
            setup.restore_profile_prefs(self.root)
        self.assertEqual(path.read_text(encoding="utf-8"), original)

    def test_host_preserves_installer_and_future_fields(self):
        self.config.write_text('{"userChromeEnabled": false, "future": {"keep": true}}', encoding="utf-8")
        with patch.object(host, "CONFIG_PATH", self.config):
            self.assertTrue(host.save_config(host.Config(), {"debug": True}))
        data = json.loads(self.config.read_text(encoding="utf-8"))
        self.assertFalse(data["userChromeEnabled"])
        self.assertEqual(data["future"], {"keep": True})


if __name__ == "__main__":
    unittest.main(verbosity=2)
