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
    marker = "/* v2.15 — live Matugen and calm glass polish */"
    assert marker in css
    return css.split(marker, 1)[1]


def test_matugen_palette_is_reloaded_without_restarting_adaptive_glass():
    app = read("app.tsx")

    assert 'const matugenDir = `${home}/.config/matugen/generated`' in app
    assert 'const matugenPath = `${matugenDir}/waybar-colors.css`' in app
    assert "function composeCss()" in app
    assert "startMatugenMonitor()" in app
    assert "monitor_directory" in app
    assert 'changedName !== "waybar-colors.css"' in app
    assert "app.apply_css(composeCss(), true)" in app


def test_clock_calendar_media_and_launcher_use_the_final_compact_polish_layer():
    css = final_css()

    clock_24h = css_block(css, ".clock-card.clock-mode-24h")
    calendar_popup = css_block(css, ".popup-calendar")
    calendar_label = css_block(css, ".calendar-date-label")
    media_card = css_block(css, ".media-card")
    media_art = css_block(css, ".media-art-frame")
    launcher_light = css_block(css, ".theme-light .launcher-card")

    assert "min-width: 72px" in clock_24h
    assert "padding: 0 8px" in clock_24h
    assert "border-radius: 22px" in calendar_popup
    assert "font-size: 13px" in calendar_label
    assert "0 2px 7px alpha(#000000, 0.18)" in media_card
    assert "box-shadow: none" in media_art
    assert "border-color: transparent" in launcher_light
    assert "background: transparent" in launcher_light


def test_workspace_hover_uses_slow_transform_not_layout_width_animation():
    css = final_css()

    button = css_block(css, ".workspace-button")
    expanded = css_block(css, ".workspace-button.expanded")
    hovered = css_block(css, ".workspace-button.hovered")
    soft = css_block(css, ".motion-soft-magnetic .workspace-button")
    precise = css_block(css, ".motion-precise-futuristic .workspace-button")

    assert "transition:" in button
    assert "min-width" not in button.split("transition:", 1)[1]
    assert "padding: 0 2px" in expanded
    assert "transform: scaleX(1.16)" in hovered
    assert "560ms cubic-bezier" in soft
    assert "260ms cubic-bezier" in precise
