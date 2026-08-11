from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

def read(path):
    return (ROOT / path).read_text()


def test_shared_theme_state_is_persistent_and_uses_dusky_backend():
    p = ROOT / "lib/themeState.ts"
    assert p.exists(), "themeState.ts must exist"
    text = p.read_text()
    assert "createState" in text
    assert "adaptive-glass-theme" in text
    assert "runTheme" in text
    assert "setAdaptiveTheme" in text


def test_bar_and_popups_receive_reactive_theme_classes():
    bar = read("components/Bar.tsx")
    popup = read("components/PopupWindow.tsx")
    assert "themeMode" in bar and "theme-light" in bar and "theme-dark" in bar
    assert "themeMode" in popup and "theme-light" in popup and "theme-dark" in popup


def test_display_uses_one_toggle_not_two_theme_choice_buttons():
    text = read("components/DisplayControl.tsx")
    assert "<Gtk.Switch" in text
    assert "display-theme-switch" in text
    assert "setAdaptiveTheme" in text
    assert "display-theme-choice light" not in text
    assert "display-theme-choice dark" not in text


def test_light_mode_styles_entire_shell_not_only_display_panel():
    css = read("style.css")
    assert ".theme-light .bar-shell" in css
    assert ".theme-light .popup-window-frame" in css
    assert ".theme-light .launcher-card" in css
    assert ".theme-light .workspace-deck" in css
    assert ".theme-light .media-card" in css
    assert ".theme-light .control-panel" in css
    assert ".theme-light .calendar-widget" in css
    assert ".theme-light switch.display-theme-switch" in css
