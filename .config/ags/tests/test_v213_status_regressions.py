from pathlib import Path
import re

AGS_ROOT = Path(__file__).resolve().parents[1]
REPO_ROOT = AGS_ROOT.parents[1]


def read_ags(rel: str) -> str:
    return (AGS_ROOT / rel).read_text()


def read_repo(rel: str) -> str:
    return (REPO_ROOT / rel).read_text()


def css_block(css: str, selector: str) -> str:
    matches = re.findall(r"(?m)^" + re.escape(selector) + r"\s*\{(.*?)\}", css, re.S)
    assert matches, f"missing CSS block for {selector}"
    return matches[-1]


def final_css() -> str:
    css = read_ags("style.css")
    marker = "/* v2.12 reference primary glow capsule polish */"
    assert marker in css
    return css.split(marker, 1)[1]


def test_clock_12h_meridiem_is_overlay_and_cannot_expand_the_pill_rightward():
    tsx = read_ags("components/ClockCard.tsx")
    css = final_css()

    card_12h = css_block(css, ".clock-card.clock-mode-12h")
    content_12h = css_block(css, ".clock-card.clock-mode-12h .clock-card-content")
    meridiem = css_block(css, ".clock-meridiem")

    assert "clockCardClass" in tsx
    assert "clock-mode-12h" in tsx
    assert 'class="clock-divider"' not in tsx
    assert '$type="overlay"' in tsx
    assert 'class="clock-meridiem"' in tsx
    assert "min-width: 84px" in card_12h
    assert "padding: 0 8px" in card_12h
    assert "margin-right: 14px" in content_12h
    assert "min-width: 16px" in meridiem


def test_battery_inner_level_is_brighter_than_the_reference_ring():
    css = final_css()

    glass = css_block(css, ".battery-waybar05-glass")
    level = css_block(css, ".battery-waybar05-level")

    assert "alpha(@primary, 0.32)" in glass
    assert "alpha(@primary, 0.96)" in level
    assert "alpha(@tertiary, 0.76)" in level
    assert "0 0 10px alpha(@primary, 0.36)" in level


def test_adaptive_theme_calls_theme_controller_directly_so_mode_switch_changes_wallpaper():
    dusky = read_ags("lib/dusky.ts")

    run_theme = re.search(r"export function runTheme\(.*?\n\}", dusky, re.S)
    assert run_theme, "missing runTheme helper"
    body = run_theme.group(0)

    assert "theme_ctl.sh" in body
    assert "set --mode ${mode}" in body
    assert "dusky-run" not in body


def test_raw_hyprland_config_exposes_bar_toggle_keybind_without_waiting_for_lua_regeneration():
    lua = read_repo(".config/hypr/source/keybinds.lua")
    conf = read_repo(".config/hypr/hyprland.conf")

    assert "SUPER + ALT + G" in lua
    assert "bar/bar_switch.sh toggle" in lua
    assert "bar/bar_switch.sh toggle" in conf
    assert "Toggle bar: Waybar <-> Adaptive Glass" in conf
    assert "$mainMod ALT, G" in conf
