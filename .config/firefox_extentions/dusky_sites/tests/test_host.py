"""Focused host regressions; run with Python 3.15 unittest discovery."""
import importlib.util
import json
import os
from pathlib import Path
import struct
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('dusky_host_extension_tests', Path(os.environ.get('DUSKY_HOST_SOURCE', ROOT / 'dusky_sites_host.py')))
host = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = host
spec.loader.exec_module(host)


class HostTests(unittest.TestCase):
    def test_inotify_directory_aliases_share_one_registration(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            directory = root / 'real'
            directory.mkdir()
            alias = root / 'alias'
            alias.symlink_to(directory, target_is_directory=True)
            watcher = host.Inotify()
            try:
                self.assertTrue(watcher.sync({directory, alias}))
                self.assertEqual(len(watcher._by_dir), 1)
                self.assertEqual(len(watcher._by_wd), 1)
                self.assertTrue(watcher.sync({alias}))
                self.assertEqual(len(watcher._by_dir), 1)
            finally:
                os.close(watcher.fd)

    def test_internal_about_styles_are_not_website_templates(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / 'about.css').write_text('@-moz-document regexp("about:.*") {body{color:red}}', encoding='utf-8')
            (root / 'example.com.css').write_text('body{color:blue}', encoding='utf-8')
            rules = host.SiteRules(root)
            self.assertTrue(rules.refresh())
            self.assertEqual(rules.sites, {'example.com': 'body{color:blue}'})

    def test_invalid_patched_path_cannot_poison_config(self):
        for value in ('relative/path', '~__dusky_missing_test_user__/colors.css', '/bad\0path'):
            with self.subTest(value=value):
                cfg = host.Config()
                self.assertFalse(cfg.apply({'colorsPath': value}))
                self.assertEqual(cfg.colors_path, host.Config().colors_path)

    def test_complete_frame_before_eof_is_not_dropped(self):
        wire = host.Wire()
        message = json.dumps({'type': 'PING'}).encode()
        # Simulate one complete read buffer followed immediately by EOF.
        # Padding is legal JSON whitespace and makes the read exactly 64 KiB.
        message += b' ' * (len(wire._buf) - 4 - len(message))
        data = struct.pack('=I', len(message)) + message
        def readinto(_fd, buffer):
            if readinto.sent:
                return 0
            readinto.sent = True
            buffer[:len(data)] = data
            return len(data)
        readinto.sent = False
        with patch.object(os, 'readinto', readinto):
            self.assertEqual(wire.read(), [{'type': 'PING'}])
            self.assertIsNone(wire.read())

    def test_fragmented_wire_and_multiple_frames(self):
        message = {'type': 'PING', 'at': 42}
        body = json.dumps(message).encode()
        frame = struct.pack('=I', len(body)) + body
        chunks = [frame[:3], frame[3:11], frame[11:] + frame]
        wire = host.Wire()
        def readinto(_fd, buffer):
            if not chunks:
                raise BlockingIOError
            chunk = chunks.pop(0)
            buffer[:len(chunk)] = chunk
            return len(chunk)
        with patch.object(os, 'readinto', readinto):
            self.assertEqual(wire.read(), [])
            self.assertEqual(wire.read(), [])
            self.assertEqual(wire.read(), [message, message])

    def test_chunked_unicode_wire_roundtrip(self):
        wire = host.Wire()
        message = {'type': 'MATUGEN_UPDATE', 'css': '界😀' * 200000}
        frames = []
        with patch.object(wire, '_write', side_effect=lambda data: frames.append(json.loads(data)) or True):
            self.assertTrue(wire.send(message))
        self.assertGreater(len(frames), 1)
        self.assertTrue(all(len(json.dumps(frame, ensure_ascii=False, separators=(',', ':')).encode()) < host.MAX_MSG for frame in frames))
        self.assertEqual(json.loads(''.join(frame['part'] for frame in frames)), message)

    def test_site_parser_ignores_commented_directives(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'example.css'
            path.write_text('/* @-moz-document domain("wrong.test") {body{color:red}} */\n@-moz-document domain("right.test") {body{color:blue}}', encoding='utf-8')
            sites = {}
            host.parse_site_file(path, sites)
            self.assertEqual(sites, {'right.test': 'body{color:blue}'})

    def test_site_parser_keeps_mixed_domain_and_url_hosts(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'example.css'
            path.write_text('@-moz-document domain("one.test"), url-prefix("https://two.test/") {body{color:blue}}', encoding='utf-8')
            sites = {}
            host.parse_site_file(path, sites)
            self.assertEqual(set(sites), {'one.test', 'two.test'})

    def test_fallback_respects_native_dark_site_and_hints(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / 'dark-sites.config').write_text('dark.test\n', encoding='utf-8')
            (root / 'detector-hints.config').write_text('hint.test\nMATCH\nhtml.dark\n', encoding='utf-8')
            (root / 'dynamic-theme-fixes.config').write_text('*\nCSS\n.common{color:${#000000}}\n=====\nlight.test\nINVERT\n.logo\n', encoding='utf-8')
            fallback = host.Fallback(lambda: root)
            with patch.object(host, 'SETTINGS_DIR', root):
                self.assertTrue(fallback.domain_fix('dark.test')['isDarkSite'])
                self.assertEqual(fallback.domain_fix('hint.test')['hints'], ['html.dark'])
                self.assertIn('var(--on_surface)', fallback.domain_fix('light.test')['css'])
                self.assertIn('invert(1)', fallback.domain_fix('light.test')['css'])

    def test_parse_colors_strips_comments_without_corrupting_values(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'palette.css'
            path.write_text('/* --primary: #ff0000; */\n:root { --dusky-palette-primary: #112233; /* inline */ --note: "/* keep */"; }\n', encoding='utf-8')
            colors = host.parse_colors(path)
            self.assertEqual(colors['--primary'], '#112233')
            self.assertEqual(colors['--note'], '"/* keep */"')

    def test_site_parser_concatenates_stems_and_filters_url_scheme_and_port(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            a, b = root / 'foo.css', root / 'sub' / 'foo.css'
            b.parent.mkdir()
            a.write_text('body{color:red}', encoding='utf-8')
            b.write_text('body{color:blue}', encoding='utf-8')
            sites = {}
            host.parse_site_file(a, sites)
            host.parse_site_file(b, sites)
            self.assertEqual(sites['foo'], 'body{color:red}\nbody{color:blue}')
            c = root / 'urls.css'
            c.write_text('@-moz-document url-prefix("https://app.test:8443/x"), url("about:preferences") {body{color:green}}', encoding='utf-8')
            sites2 = {}
            host.parse_site_file(c, sites2)
            self.assertEqual(sites2, {'app.test': 'body{color:green}'})

    def test_site_rules_detect_inode_replacement_with_same_mtime_and_size(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            target = root / 'example.com.css'
            target.write_text('body{color:red}', encoding='utf-8')
            st = target.stat()
            rules = host.SiteRules(root)
            self.assertTrue(rules.refresh())
            replacement = root / 'replacement.tmp'
            replacement.write_text('body{color:tan}', encoding='utf-8')
            os.utime(replacement, ns=(st.st_atime_ns, st.st_mtime_ns))
            replacement.replace(target)
            self.assertTrue(rules.refresh())
            self.assertEqual(rules.sites['example.com'], 'body{color:tan}')

    def test_fallback_wildcard_tld_and_header_comments(self):
        self.assertGreater(host.domain_score('mail.google.com', 'mail.google.*'), 0)
        self.assertGreater(host.domain_score('www.google.co.uk', 'google.*'), 0)
        self.assertGreater(host.domain_score('sub.mail.google.com.au', 'mail.google.*'), 0)
        self.assertEqual(host.domain_score('notgoogle.com', 'google.*'), 0)
        self.assertEqual(host.domain_score('evil.google.com.attacker.org', 'google.*'), 0)
        self.assertEqual(host.domain_score('google.attacker.uk', 'google.*'), 0)
        blocks = host.parse_blocks('# header comment\nexample.test\nCSS\n#id { color: red; }\n')
        self.assertEqual(blocks[0][0], ['example.test'])
        self.assertEqual(blocks[0][1]['CSS'], ['#id { color: red; }'])

    def test_wire_write_returns_false_on_select_timeout(self):
        with patch.object(os, 'write', side_effect=BlockingIOError), \
             patch.object(host.select, 'select', return_value=([], [], [])):
            self.assertFalse(host.Wire._write(b'{}'))

    def test_save_config_preserves_unknown_keys(self):
        with tempfile.TemporaryDirectory() as tmp:
            cfg_path = Path(tmp) / 'config.json'
            cfg_path.write_text(json.dumps({'installerFlag': True, 'webThemeEnabled': False}), encoding='utf-8')
            with patch.object(host, 'CONFIG_PATH', cfg_path):
                cfg = host.Config()
                self.assertTrue(host.save_config(cfg, {'webThemeEnabled': True}))
                saved = json.loads(cfg_path.read_text(encoding='utf-8'))
                self.assertTrue(saved['installerFlag'])
                self.assertTrue(saved['webThemeEnabled'])

    def test_write_live_theme_deduplicates_and_recreates_if_deleted(self):
        with tempfile.TemporaryDirectory() as tmp:
            live_path = Path(tmp) / 'live_theme_cache.json'
            dummy = object.__new__(host.Host)
            dummy._live_theme_canon = None
            with patch.object(host, 'LIVE_THEME_PATH', live_path), \
                 patch.object(host, 'atomic_write', wraps=host.atomic_write) as spy:
                dummy.write_live_theme({'colors': {'frame': '#112233'}})
                dummy.write_live_theme({'colors': {'frame': '#112233'}})
                self.assertEqual(spy.call_count, 1)
                live_path.unlink()
                dummy.write_live_theme({'colors': {'frame': '#112233'}})
                self.assertEqual(spy.call_count, 2)


if __name__ == '__main__':
    unittest.main()
