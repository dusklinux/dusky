from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def read(path: str) -> str:
    return (ROOT / path).read_text()


def test_theme_control_uses_native_gtk_switch_with_dark_on_right():
    text = read("components/DisplayControl.tsx")
    assert "<Gtk.Switch" in text
    assert 'class="display-theme-switch"' in text
    assert 'active={themeMode((mode) => mode === "dark")}' in text
    assert "onStateSet" in text
    assert 'setAdaptiveTheme(state ? "dark" : "light")' in text
    assert "display-theme-knob" not in text
    assert "display-theme-track" not in text


def test_frosted_mist_uses_layered_cool_surfaces_not_white_sheets():
    css = read("style.css")
    # Named markers make the intended light palette explicit and regression-safe.
    assert "v2.2.2 — Frosted Mist" in css
    for color in ("#d7e3e9", "#c8d7df", "#bccdd7", "#243640", "#526570"):
        assert color in css
    # The main shell and popup fills should no longer be white-dominant.
    light_block = css[css.index("/* v2.2.2 — Frosted Mist"):]
    assert "alpha(#ffffff, 0.88)" not in light_block
    assert "alpha(#ffffff, 0.90)" not in light_block
    assert "alpha(#fbfdff, 0.985)" not in light_block


def test_native_switch_has_clear_checked_hover_and_slider_states():
    css = read("style.css")
    assert "switch.display-theme-switch" in css
    assert "switch.display-theme-switch:checked" in css
    assert "switch.display-theme-switch:hover" in css
    assert "switch.display-theme-switch slider" in css
    assert "switch.display-theme-switch:checked slider" in css


def test_light_mode_still_covers_bar_and_popup_surfaces():
    css = read("style.css")
    for selector in (
        ".theme-light .launcher-card",
        ".theme-light .workspace-deck",
        ".theme-light .media-card",
        ".theme-light .popup-window-frame",
        ".theme-light .calendar-widget",
        ".theme-light .network-connection-card",
        ".theme-light .audio-channel",
        ".theme-light .display-brightness-card",
    ):
        assert selector in css
