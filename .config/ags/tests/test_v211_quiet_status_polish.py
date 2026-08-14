from pathlib import Path
import re

ROOT = Path(__file__).resolve().parents[1]


def read(rel: str) -> str:
    return (ROOT / rel).read_text()


def css_block(css: str, selector: str) -> str:
    matches = re.findall(r"(?m)^" + re.escape(selector) + r"\s*\{(.*?)\}", css, re.S)
    assert matches, f"missing CSS block for {selector}"
    return matches[-1]


def test_v211_final_cascade_marker_and_workspace_hover_does_not_retrigger_bloom():
    css = read("style.css")
    final = css.split("/* v2.11 quiet status polish */", 1)[1]

    active = css_block(css, ".workspace-button.active")
    hovered = css_block(css, ".workspace-button.hovered")
    soft_active = css_block(css, ".motion-soft-magnetic .workspace-button.active")
    precise_active = css_block(css, ".motion-precise-futuristic .workspace-button.active")

    assert "workspace-v29-liquid-bloom" not in final
    assert "cubic-bezier(0.34, 1.56" not in final
    assert "animation: none" in active
    assert "animation: none" in hovered
    assert "animation: none" in soft_active
    assert "animation: none" in precise_active


def test_v211_clock_is_compact_notch_with_hour_only_glint():
    css = read("style.css")

    card = css_block(css, ".clock-card")
    line = css_block(css, ".clock-hour-line")
    changed_line = css_block(css, ".clock-hour-line.hour-changed")

    assert "margin-top: -2px" in card
    assert "min-width: 84px" in card
    assert "border-radius: 0 0 12px 12px" in card
    assert "box-shadow:" in card
    assert "opacity: 0" in line
    assert "min-width: 46px" in line
    assert "clock-v211-hour-glint" in changed_line


def test_v211_battery_is_quiet_horizontal_gauge_without_alarm_glyph():
    tsx = read("components/Battery.tsx")
    css = read("style.css")

    card = css_block(css, ".battery-card")
    level = css_block(css, ".battery-waybar05-level")
    empty = css_block(css, ".battery-card.battery-level-empty")

    assert "battery-warning-sign" not in tsx
    assert 'label="!"' not in tsx
    assert "border-radius: 16px" in card
    assert "animation: none" in level
    assert "battery-v28-warning-pulse" in empty
    assert "battery-v29-zero-glow" not in empty
    assert "infinite" not in empty


def test_v211_power_trigger_is_neutral_and_centered():
    css = read("style.css")

    leader = css_block(css, ".power-leader")
    icon = css_block(css, ".power-trigger-icon")
    dot = css_block(css, ".power-trigger-status-dot")

    assert "@error" not in leader
    assert "@error" not in icon
    assert "color: alpha(@on_surface_variant, 0.88)" in icon
    assert "text-shadow: none" in icon
    assert "opacity: 0" in dot
    assert "box-shadow: none" in dot
    assert "background: transparent" in dot
