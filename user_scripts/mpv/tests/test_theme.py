"""Render Matugen palettes and exercise the real controller through mpv IPC."""
import json
import os
from pathlib import Path
import shutil
import socket
import subprocess
import tempfile
import time
import unittest

ROOT = Path(__file__).resolve().parents[1]
TEMPLATE = ROOT.parents[1] / '.config/matugen/templates/mpv.json'


class ThemeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.generated = tempfile.TemporaryDirectory(prefix='mpv-theme-palettes-')
        cls.addClassCleanup(cls.generated.cleanup)
        folder = Path(cls.generated.name)
        cls.palettes = {}
        for mode in ('dark', 'light'):
            output = folder / f'{mode}.json'
            config = folder / f'{mode}.toml'
            config.write_text(
                f'[config]\n[templates.mpv]\ninput_path={json.dumps(str(TEMPLATE))}\n'
                f'output_path={json.dumps(str(output))}\n', encoding='utf-8')
            subprocess.run(['matugen', '-c', str(config), '-m', mode,
                            'color', 'hex', '#5588cc'], check=True,
                           capture_output=True, timeout=15)
            cls.palettes[mode] = json.loads(output.read_text(encoding='utf-8'))

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='mpv-theme-test-')
        self.addCleanup(self.tmp.cleanup)
        self.folder = Path(self.tmp.name)
        self.config = self.folder / 'mpv'
        shutil.copytree(ROOT / 'setup/config', self.config)
        shutil.copytree(ROOT / 'setup/assets', self.config, dirs_exist_ok=True)
        main = self.config / 'scripts/dusky_player/main.lua'
        with main.open('a', encoding='utf-8') as file:
            file.write('''
mp.register_script_message('theme-test', function(token)
    local titles = {}
    if Elements.menu then
        for _, item in ipairs(Elements.menu.current.items) do
            titles[#titles + 1] = item.ass_safe_title or ''
        end
    end
    mp.set_property_native('user-data/theme-test', {
        token = token, colors = config.color, ass = osd.data,
        theme_colors = theme.colors, search_titles = table.concat(titles, '\\n'),
    })
end)
mp.register_script_message('theme-test-search', function(query)
    local menu = Menu:is_open()
    if menu then menu:search_query_replace(query) end
end)
mp.register_script_message('theme-test-reload', function() theme:reload() end)
''')
        self.palette_path = self.folder / 'matugen/generated/mpv.json'
        self.palette_path.parent.mkdir(parents=True)

    def start(self, extra=()):
        log = (self.folder / 'mpv.log').open('w+', encoding='utf-8')
        self.addCleanup(log.close)
        self.log = log
        self.process = subprocess.Popen([
            'mpv', f'--config-dir={self.config}', '--vo=null', '--ao=null',
            '--force-window=no', '--idle=yes', '--terminal=no',
            f'--input-ipc-server={self.folder / "ipc"}', *extra,
        ], stdout=log, stderr=subprocess.STDOUT,
            env={**os.environ, 'XDG_CONFIG_HOME': str(self.folder)})
        self.addCleanup(self.stop)
        self.sock = socket.socket(socket.AF_UNIX)
        self.addCleanup(self.sock.close)
        self.sock.settimeout(4)
        deadline = time.monotonic() + 5
        while True:
            try:
                self.sock.connect(str(self.folder / 'ipc'))
                break
            except (FileNotFoundError, ConnectionRefusedError):
                if time.monotonic() > deadline:
                    self.fail('mpv IPC did not start')
                time.sleep(0.02)
        self.stream = self.sock.makefile('rb')
        self.addCleanup(self.stream.close)
        self.request_id = 0

    def stop(self):
        if self.process.poll() is None:
            self.process.terminate()
        self.process.wait(timeout=5)
        self.log.flush()
        self.log.seek(0)
        self.assertNotIn('Lua error', self.log.read())

    def command(self, *args):
        self.request_id += 1
        self.sock.sendall((json.dumps({'command': args, 'request_id': self.request_id}) + '\n').encode())
        while True:
            response = json.loads(self.stream.readline())
            if response.get('request_id') == self.request_id:
                self.assertEqual(response['error'], 'success', response)
                return response.get('data')

    def inspect(self):
        token = str(self.request_id)
        self.command('script-message-to', 'dusky_player', 'theme-test', token)
        deadline = time.monotonic() + 3
        while time.monotonic() < deadline:
            data = self.command('get_property', 'user-data').get('theme-test', {})
            if data.get('token') == token:
                return data
            time.sleep(0.01)
        self.fail('Controller did not respond')

    def publish(self, palette):
        pending = self.palette_path.with_suffix('.pending')
        pending.write_text(json.dumps(palette), encoding='utf-8')
        pending.replace(self.palette_path)

    @staticmethod
    def ass(hex_color):
        rgb = hex_color[1:]
        return rgb[4:6] + rgb[2:4] + rgb[0:2]

    def await_palette(self, palette):
        deadline = time.monotonic() + 4
        while time.monotonic() < deadline:
            data = self.inspect()
            if data['colors']['foreground'] == self.ass(palette['foreground']):
                for key, value in palette.items():
                    self.assertEqual(data['colors'][key], self.ass(value), key)
                return data
            time.sleep(0.05)
        self.fail('Live palette did not update')

    def test_live_reload_missing_partial_invalid_and_search(self):
        self.start()
        self.assertEqual(self.inspect()['colors']['foreground'], 'ffffff')
        dark, light = self.palettes['dark'], self.palettes['light']
        self.publish(dark)
        self.await_palette(dark)
        self.command('script-binding', 'dusky_player/keybinds')
        self.command('script-message-to', 'dusky_player', 'theme-test-search', 'seek')
        time.sleep(0.1)
        self.assertIn(self.ass(dark['match']), self.inspect()['search_titles'])
        self.palette_path.write_text('{"foreground":', encoding='utf-8')
        time.sleep(2.1)
        self.assertEqual(self.inspect()['colors']['foreground'], self.ass(dark['foreground']))
        malformed = {**light, 'error': '#nothex'}
        self.publish(malformed)
        time.sleep(2.1)
        self.assertEqual(self.inspect()['colors']['foreground'], self.ass(dark['foreground']))
        self.publish(light)
        self.await_palette(light)
        self.assertIn(self.ass(light['match']), self.inspect()['search_titles'])
        self.assertNotIn(self.ass(dark['match']), self.inspect()['search_titles'])
        # Native OSD and background are updated by the same palette transaction.
        self.assertEqual(self.command('get_property', 'osd-color').upper(),
                         '#FF' + light['background_text'][1:].upper())
        self.assertEqual(self.command('get_property', 'background-color').upper(),
                         '#FF' + light['curtain'][1:].upper())
        native_options = self.command('get_property', 'options/script-opts')
        self.assertEqual(native_options['console-focused_back_color'], light['foreground'])
        self.assertEqual(native_options['stats-plot_color'], self.ass(light['foreground']))
        self.palette_path.unlink()
        time.sleep(2.1)
        self.assertEqual(self.inspect()['colors']['foreground'], self.ass(light['foreground']))
        self.publish(dark)
        self.await_palette(dark)

    def test_explicit_color_override_and_fast_equal_size_replacement(self):
        self.publish(self.palettes['dark'])
        self.start(['--script-opts-append=dusky_player-color=foreground=123456'])
        self.assertEqual(self.inspect()['colors']['foreground'], '563412')
        self.publish(self.palettes['light'])
        time.sleep(2.1)
        data = self.inspect()
        self.assertEqual(data['colors']['foreground'], '563412')
        self.assertEqual(self.command('get_property', 'options/script-opts')['dusky_player-color'],
                         'foreground=123456')
        self.assertEqual(data['colors']['background'], self.ass(self.palettes['light']['background']))

    def test_repeated_equal_size_reloads_with_open_search(self):
        self.publish(self.palettes['dark'])
        self.start()
        self.command('script-binding', 'dusky_player/keybinds')
        self.command('script-message-to', 'dusky_player', 'theme-test-search', 'seek')
        for index in range(100):
            palette = self.palettes['light' if index % 2 else 'dark']
            self.publish(palette)
            os.utime(self.palette_path, (1000, 1000))
            self.command('script-message-to', 'dusky_player', 'theme-test-reload')
            data = self.inspect()
            self.assertEqual(data['colors']['background'], self.ass(palette['background']))
            self.assertIn(self.ass(palette['match']), data['search_titles'])

    def test_disabled(self):
        self.publish(self.palettes['dark'])
        self.start(['--script-opts-append=dusky_player-theme_path='])
        self.assertEqual(self.inspect()['colors']['foreground'], 'ffffff')
        self.assertEqual(self.command('get_property', 'osd-color').upper(), '#FFFFFFFF')


if __name__ == '__main__':
    unittest.main()
