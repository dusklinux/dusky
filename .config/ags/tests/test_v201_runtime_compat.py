from pathlib import Path
import re

ROOT = Path(__file__).resolve().parents[1]


def test_network_uses_gtk_separator_not_unknown_jsx_intrinsic():
    ts = (ROOT / "components/NetworkControl.tsx").read_text()
    assert "<separator" not in ts
    assert ts.count("<Gtk.Separator") >= 3


def test_gtk_css_contains_no_unsupported_web_layout_properties():
    css = (ROOT / "style.css").read_text()
    assert not re.search(r"(?m)^\s*overflow\s*:", css)
    assert not re.search(r"(?m)^\s*max-width\s*:", css)
