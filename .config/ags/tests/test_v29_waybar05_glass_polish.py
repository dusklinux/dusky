from pathlib import Path
import re

ROOT = Path(__file__).resolve().parents[1]


def read(rel: str) -> str:
    return (ROOT / rel).read_text()


def css_block(css: str, selector: str) -> str:
    matches = re.findall(r"(?m)^" + re.escape(selector) + r"\s*\{(.*?)\}", css, re.S)
    assert matches, f"missing CSS block for {selector}"
    return matches[-1]


def test_workspace_rail_supports_ten_slots_and_waybar05_liquid_pacman():
    tsx = read("components/Workspaces.tsx")
    css = read("style.css")
    legacy, tail = css.split("/* v2.9 tail override - actual final cascade surface */", 1)

    assert "Array.from({ length: 10 }" in tsx
    assert "workspace-v29-liquid-bloom" in css
    assert not re.findall(r"(?m)^\.workspace-button\.workspace-id-\d+\.active \.workspace-pacman\s*\{", legacy)
    assert not re.findall(r"(?m)^\.theme-light \.workspace-button\.workspace-id-\d+\.active \.workspace-pacman\s*\{", legacy)
    assert not re.findall(r"(?m)^\.workspace-button\.workspace-id-\d+ \.workspace-number\s*\{", tail)
    active = css_block(css, ".workspace-button.active")
    number = css_block(css, ".workspace-number")
    assert "border-radius: 12px" in active
    assert "inset 0 0 12px" in active
    assert "color: alpha(@on_surface_variant, 0.84)" in number
    for workspace_id in range(1, 11):
        assert f".workspace-button.workspace-id-{workspace_id}.active .workspace-pacman" in tail


def test_clock_has_slower_reel_and_screen_attached_glow_rails():
    tsx = read("components/ClockCard.tsx")
    css = read("style.css")

    assert "clock-edge-glow left" in tsx
    assert "clock-edge-glow right" in tsx
    card = css_block(css, ".clock-card")
    new_face = css_block(css, ".clock-reel-digit.changed .clock-reel-new")
    old_face = css_block(css, ".clock-reel-digit.changed .clock-reel-old")
    left = css_block(css, ".clock-edge-glow.left")
    right = css_block(css, ".clock-edge-glow.right")
    assert "min-width: 98px" in card
    assert "border-radius: 0 0 10px 10px" in card
    assert "clock-v29-edge-glow" in css
    assert "#4cc9ff" not in left
    assert "#ff4fd8" not in right
    assert "alpha(@on_surface" in left
    assert "alpha(@primary" in right
    assert "880ms" in new_face
    assert "880ms" in old_face


def test_launcher_has_final_gem_treatment_for_left_edge_anchor():
    tsx = read("components/Launcher.tsx")
    css = read("style.css")

    assert 'class="launcher-glyph"' in tsx
    card = css_block(css, ".launcher-card")
    glyph = css_block(css, ".launcher-glyph")
    assert "min-width: 32px" in card
    assert "border-radius: 15px" in card
    assert "#4cc9ff" in card
    assert "#00ffd1" in card
    assert "text-shadow:" in glyph


def test_wifi_signal_changes_use_motion_classes_not_static_color_only():
    tsx = read("components/NetworkControl.tsx")
    css = read("style.css")

    assert "wifiSignalMotionClass" in tsx
    for name in ("offline", "weak", "ok", "strong"):
        assert f"wifi-signal-shift-{name}" in tsx
        assert f".wifi-signal-shift-{name}" in css
        assert f"wifi-v29-signal-{name}" in css


def test_battery_is_horizontal_shell_with_inside_percent_and_zero_glow():
    tsx = read("components/Battery.tsx")
    css = read("style.css")

    assert "battery-level-empty" in tsx
    assert "battery-shell" in tsx
    assert "battery-fill" in tsx
    assert "battery-shell-base" in tsx
    assert "battery-icon" not in tsx
    assert "battery-v29-zero-glow" in css
    assert "battery-v29-fill-sheen" in css
    empty = css_block(css, ".battery-card.battery-level-empty")
    shell = css_block(css, ".battery-shell")
    percent = css_block(css, ".battery-percent")
    assert "battery-v29-zero-glow" in empty
    assert "min-width:" in shell
    assert "min-height:" in shell
    assert "font-size:" in percent


def test_power_trigger_is_centered_overlay_orb():
    tsx = read("components/PowerControl.tsx")
    css = read("style.css")

    assert '<overlay class="power-trigger-content">' in tsx
    assert "power-trigger-orb" in tsx
    assert 'label="⏻"' in tsx
    assert "power-v29-orb-glow" in css
    content = css_block(css, ".power-trigger-content")
    shell = css_block(css, ".power-trigger-shell")
    assert "min-width:" in content
    assert "min-height:" in content
    assert "border-radius:" in shell
