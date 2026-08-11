from pathlib import Path
import re

ROOT = Path(__file__).resolve().parents[1]


def read(rel: str) -> str:
    return (ROOT / rel).read_text()


def css_block(css: str, selector: str) -> str:
    matches = re.findall(r"(?m)^" + re.escape(selector) + r"\s*\{(.*?)\}", css, re.S)
    assert matches, f"missing CSS block for {selector}"
    return matches[-1]


def keyframes(css: str, name: str) -> str:
    start = css.index(f"@keyframes {name}")
    opening = css.index("{", start)
    depth = 0
    for index in range(opening, len(css)):
        char = css[index]
        if char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                return css[start:index + 1]
    raise AssertionError(f"unterminated keyframes block for {name}")


def test_workspace_recoil_is_v27_stronger_visible_and_non_sizing():
    css = read("style.css")
    assert "v2.7 - premium workspace recoil" in css
    block = keyframes(css, "workspace-v27-premium-recoil")
    scales = [float(v) for v in re.findall(r"scaleX\((\d+(?:\.\d+)?)\)", block)]
    assert max(scales) >= 1.28
    assert any(0.88 <= value <= 0.94 for value in scales)
    for forbidden in ("padding:", "margin:", "min-width", "width:", "height:"):
        assert forbidden not in block
    assert block.count("box-shadow:") >= 4


def test_motion_modes_use_v27_recoil_timing():
    motion = read("lib/motionState.ts")
    css = read("style.css")
    assert "snapDelayMs: 120" in motion
    assert "snapPulseMs: 640" in motion
    assert "snapDelayMs: 70" in motion
    assert "snapPulseMs: 360" in motion
    soft = css_block(css, ".motion-soft-magnetic .workspace-magnetic-shell.snapping")
    precise = css_block(css, ".motion-precise-futuristic .workspace-magnetic-shell.snapping")
    assert "workspace-v27-premium-recoil" in soft
    assert "640ms" in soft
    assert "workspace-v27-premium-recoil" in precise
    assert "360ms" in precise
    assert ".theme-light.motion-soft-magnetic .workspace-magnetic-shell.snapping" in css
    assert ".theme-light.motion-precise-futuristic .workspace-magnetic-shell.snapping" in css


def test_clock_uses_fixed_reel_digits_without_accent_dot():
    tsx = read("components/ClockCard.tsx")
    assert "ClockReelDigit" in tsx
    assert "clock-reel" in tsx
    assert "clock-reel-digit" in tsx
    assert "clock-reel-separator" in tsx
    assert "clock-accent-dot" not in tsx
    assert 'panel="calendar"' in tsx


def test_clock_reel_css_has_vertical_casino_motion_and_stable_slots():
    css = read("style.css")
    assert "@keyframes clock-v27-reel-in" in css
    reel = keyframes(css, "clock-v27-reel-in")
    assert "translateY(-" in reel
    assert "translateY(0" in reel
    slot = css_block(css, ".clock-reel-digit")
    assert "min-width:" in slot
    assert "min-height:" in slot
    assert "animation: clock-v27-reel-in" in css
    assert "font-feature-settings: \"tnum\"" in css_block(css, ".clock-reel-digit-face")
    assert ".clock-accent-dot" in css
    dot = css_block(css, ".clock-accent-dot")
    assert "min-width: 0" in dot
    assert "min-height: 0" in dot
    assert "opacity: 0" in dot
    assert "box-shadow: none" in dot


def test_calendar_keeps_native_behavior_and_gets_v27_surface_polish():
    tsx = read("components/ClockCard.tsx")
    css = read("style.css")
    assert "<Gtk.Calendar" in tsx
    assert 'label="Today"' in tsx
    assert 'label="Clocks"' in tsx
    assert "calendar.select_day(GLib.DateTime.new_now_local())" in tsx
    assert "v2.7 - calendar frosted refinement" in css
    panel = css_block(css, ".calendar-panel")
    widget = css_block(css, ".calendar-widget")
    footer = css_block(css, ".calendar-footer-action")
    assert "padding: 11px" in panel
    assert "border-radius: 16px" in widget
    assert "min-height: 26px" in footer


def test_popup_frames_have_open_state_and_shared_reveal_motion():
    tsx = read("components/PopupWindow.tsx")
    css = read("style.css")
    assert "popup-open" in tsx
    assert "@keyframes popup-v27-reveal" in css
    frame = css_block(css, ".popup-window-frame")
    assert "animation: popup-v27-reveal" in frame
    assert "transform-origin: top center" in frame
    assert ".motion-soft-magnetic .popup-window-frame" in css
    assert ".motion-precise-futuristic .popup-window-frame" in css
