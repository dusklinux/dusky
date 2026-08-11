import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
COMP = ROOT / "components" / "DisplayControl.tsx"
CSS = ROOT / "style.css"

class DisplayHubV22Tests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.comp = COMP.read_text()
        cls.css = CSS.read_text()

    def test_old_action_grid_is_removed(self):
        self.assertNotIn('class="action-grid"', self.comp)
        self.assertNotIn('class="action-card"', self.comp)

    def test_brightness_uses_dedicated_compact_row_and_thick_slider(self):
        self.assertIn('class="display-brightness-row"', self.comp)
        self.assertIn('class="display-thick-slider"', self.comp)
        self.assertIn('label="Brightness"', self.comp)

    def test_theme_is_direct_light_dark_toggle(self):
        self.assertIn('<Gtk.Switch', self.comp)
        self.assertIn('display-theme-switch', self.comp)
        self.assertIn('setAdaptiveTheme', self.comp)
        self.assertNotIn('display-theme-knob', self.comp)
        self.assertNotIn('class="display-theme-segment"', self.comp)
        self.assertIn('mode === "light" ? "Light" : "Dark"', self.comp)

    def test_wallpaper_and_night_are_compact_action_rows(self):
        self.assertIn('class="display-action-row wallpaper-row"', self.comp)
        self.assertIn('class="display-action-row night-row"', self.comp)
        self.assertIn('runWallpaper()', self.comp)
        self.assertIn('runNightControls()', self.comp)
        self.assertIn('label="Wallpaper"', self.comp)
        self.assertIn('label="Night controls"', self.comp)

    def test_display_panel_width_is_compact(self):
        match = re.search(r"\.display-panel\s*\{[^}]*min-width:\s*(\d+)px", self.css, re.S)
        self.assertIsNotNone(match)
        width = int(match.group(1))
        self.assertGreaterEqual(width, 280)
        self.assertLessEqual(width, 300)

    def test_display_slider_is_thick_and_rounded(self):
        self.assertRegex(self.css, r"scale\.display-thick-slider trough\s*\{[^}]*min-height:\s*(?:10|11|12)px")
        self.assertRegex(self.css, r"scale\.display-thick-slider trough\s*\{[^}]*border-radius:\s*999px")

if __name__ == "__main__":
    unittest.main()
