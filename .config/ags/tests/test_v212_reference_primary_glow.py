from pathlib import Path
import re

ROOT = Path(__file__).resolve().parents[1]


def read(rel: str) -> str:
    return (ROOT / rel).read_text()


def css_block(css: str, selector: str) -> str:
    matches = re.findall(r"(?m)^" + re.escape(selector) + r"\s*\{(.*?)\}", css, re.S)
    assert matches, f"missing CSS block for {selector}"
    return matches[-1]


def final_css() -> str:
    css = read("style.css")
    marker = "/* v2.12 reference primary glow capsule polish */"
    assert marker in css
    return css.split(marker, 1)[1]


def test_v212_clock_matches_reference_capsule_with_matugen_primary_glow():
    css = final_css()

    card = css_block(css, ".clock-card")
    hover = css_block(css, ".clock-card:hover")
    line = css_block(css, ".clock-hour-line")

    assert "min-width: 72px" in card
    assert "min-height: 28px" in card
    assert "padding: 0 8px" in card
    assert "border-color: alpha(@primary, 0.30)" in card
    assert "border-radius: 13px" in card
    assert "alpha(@primary" in card
    assert "border-color: alpha(@primary, 0.40)" in hover
    assert "0 0 11px alpha(@primary, 0.24)" in hover
    assert "opacity: 0" in line


def test_v212_media_island_uses_dark_glass_and_primary_play_glow():
    css = final_css()
    media = read("components/MediaCard.tsx")

    card = css_block(css, ".media-card")
    copy = css_block(css, ".media-copy")
    art = css_block(css, ".media-art-frame")
    play = css_block(css, ".media-control.play")

    assert "width={22}" in media
    assert "maxWidthChars={18}" in media
    assert "min-height: 30px" in card
    assert "border-radius: 13px" in card
    assert "border-color: alpha(@primary, 0.22)" in card
    assert "0 2px 7px alpha(#000000, 0.18)" in card
    assert "min-width: 64px" in copy
    assert "min-width: 26px" in art
    assert "min-height: 26px" in art
    assert "box-shadow: none" in art
    assert "background: alpha(@primary, 0.62)" in play
    assert "color: @on_primary" in play
    assert "0 0 12px alpha(@primary, 0.42)" in play


def test_v212_controls_battery_and_power_share_primary_reference_ring():
    css = final_css()

    control = css_block(css, ".control-leader")
    battery = css_block(css, ".battery-card")
    power = css_block(css, ".power-leader")
    power_icon = css_block(css, ".power-trigger-icon")

    assert "min-width: 31px" in control
    assert "min-height: 30px" in control
    assert "border-color: alpha(@primary, 0.24)" in control
    assert "0 0 10px alpha(@primary, 0.22)" in control
    assert "border-color: alpha(@primary, 0.28)" in battery
    assert "0 0 11px alpha(@primary, 0.24)" in battery
    assert "@error" not in power
    assert "border-color: alpha(@primary, 0.24)" in power
    assert "background: alpha(@surface_container_low, 0.52)" in power
    assert "font-size: 14px" in power_icon


def test_v212_reference_pass_avoids_hardcoded_pink_for_normal_glow():
    css = final_css()

    assert "#ff4fd8" not in css
    assert "#ff5bd6" not in css
    assert "#ff65c8" not in css
