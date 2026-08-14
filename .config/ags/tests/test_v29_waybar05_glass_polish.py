from pathlib import Path
import re

ROOT = Path(__file__).resolve().parents[1]


def read(rel: str) -> str:
    return (ROOT / rel).read_text()


def css_block(css: str, selector: str) -> str:
    matches = re.findall(r"(?m)^" + re.escape(selector) + r"\s*\{(.*?)\}", css, re.S)
    assert matches, f"missing CSS block for {selector}"
    return matches[-1]


def test_workspace_rail_uses_five_dynamic_slots_and_waybar05_liquid_pacman():
    tsx = read("components/Workspaces.tsx")
    css = read("style.css")
    legacy, tail = css.split("/* v2.9 tail override - actual final cascade surface */", 1)

    assert "WORKSPACE_VISIBLE_SLOTS = [1, 2, 3, 4, 5] as const" in tsx
    assert "workspaceIdForSlot" in tsx
    assert "active() > 5 ? active() : slot" in tsx
    assert "Array.from({ length: 10 }" not in tsx
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
        assert f".workspace-button.workspace-accent-{workspace_id}.active .workspace-pacman" in tail


def test_clock_has_24h_toggle_and_single_screen_attached_hour_line():
    tsx = read("components/ClockCard.tsx")
    css = read("style.css")

    assert "clock24hEnabled" in tsx
    assert '"%H:%M"' in tsx
    assert '"%I:%M"' in tsx
    assert "clock-hour-line" in tsx
    assert "hourChanged" in tsx
    assert "clock-edge-glow" not in tsx
    card = css_block(css, ".clock-card")
    new_face = css_block(css, ".clock-reel-digit.changed .clock-reel-new")
    old_face = css_block(css, ".clock-reel-digit.changed .clock-reel-old")
    line = css_block(css, ".clock-hour-line")
    assert "min-width: 90px" in card
    assert "border-radius: 0 0 7px 7px" in card
    assert "background: alpha(@surface_container_low, 0.62)" in card
    assert "clock-v210-hour-glow" in css
    assert "alpha(@on_surface" in line
    assert "valign={Gtk.Align.START}" in tsx
    assert "880ms" in new_face
    assert "880ms" in old_face


def test_launcher_is_bare_modern_arch_symbol():
    tsx = read("components/Launcher.tsx")
    css = read("style.css")

    assert 'class="launcher-glyph"' in tsx
    assert 'label="󰣇"' in tsx
    card = css_block(css, ".launcher-card")
    glyph = css_block(css, ".launcher-glyph")
    assert "background: transparent" in card
    assert "border: none" in card
    assert "box-shadow: none" in card
    assert "text-shadow:" in glyph


def test_wifi_signal_changes_use_motion_classes_not_static_color_only():
    tsx = read("components/NetworkControl.tsx")
    css = read("style.css")

    assert "wifiSignalMotionClass" in tsx
    for name in ("offline", "weak", "ok", "strong"):
        assert f"wifi-signal-shift-{name}" in tsx
        assert f".wifi-signal-shift-{name}" in css
        assert f"wifi-v29-signal-{name}" in css


def test_battery_uses_waybar05_module_style_with_inside_percent_and_zero_glow():
    tsx = read("components/Battery.tsx")
    css = read("style.css")

    assert "battery-level-empty" in tsx
    assert "battery-waybar05-level" in tsx
    assert "battery-waybar05-glass" in tsx
    assert "battery-shell" not in tsx
    assert "battery-icon" not in tsx
    assert "battery-v29-zero-glow" in css
    assert "battery-v29-fill-sheen" in css
    empty = css_block(css, ".battery-card.battery-level-empty")
    level = css_block(css, ".battery-waybar05-level")
    percent = css_block(css, ".battery-percent")
    assert "battery-v29-zero-glow" in empty
    assert "min-width:" in level
    assert "min-height:" in level
    assert "font-size:" in percent


def test_power_trigger_is_normalized_to_control_leader_surface():
    tsx = read("components/PowerControl.tsx")
    css = read("style.css")

    assert '<overlay class="power-trigger-content">' in tsx
    assert "power-trigger-orb" not in tsx
    assert "power-trigger-shell" not in tsx
    assert 'label="⏻"' in tsx
    content = css_block(css, ".power-trigger-content")
    assert "min-width:" in content
    assert "min-height:" in content
