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


def test_clock_reel_uses_previous_and_current_faces_with_tick_classes():
    tsx = read("components/ClockCard.tsx")
    css = read("style.css")

    assert "previousTime" in tsx
    assert "clockReelTick" in tsx
    assert "clock-reel-old" in tsx
    assert "clock-reel-new" in tsx
    assert "clock-reel-tick-even" in tsx
    assert "clock-reel-tick-odd" in tsx
    assert "@keyframes clock-v28-reel-old" in css
    assert "@keyframes clock-v28-reel-new" in css
    assert "clock-v28-reel-old" in css_block(css, ".clock-reel-digit.changed .clock-reel-old")
    assert "clock-v28-reel-new" in css_block(css, ".clock-reel-digit.changed .clock-reel-new")


def test_popup_reveal_has_no_sideways_scale_or_transform_transition():
    css = read("style.css")

    assert "@keyframes popup-v28-stable-reveal" in css
    reveal = keyframes(css, "popup-v28-stable-reveal")
    frame = css_block(css, ".popup-window-frame")
    soft = css_block(css, ".motion-soft-magnetic .popup-window-frame")
    precise = css_block(css, ".motion-precise-futuristic .popup-window-frame")
    assert "scale(" not in reveal
    assert "animation: popup-v28-stable-reveal" in frame
    assert "transition:" not in frame
    assert "transform" not in soft
    assert "transform" not in precise


def test_workspace_active_indicator_is_pacman_and_color_indexed():
    tsx = read("components/Workspaces.tsx")
    css = read("style.css")

    assert "workspace-id-${id}" in tsx
    assert "workspace-pacman" in tsx
    assert 'label="󰮯"' in tsx
    assert "workspace-active-dot" not in tsx
    for workspace_id in range(1, 11):
        assert f".workspace-button.workspace-id-{workspace_id}.active .workspace-pacman" in css


def test_network_icon_uses_signal_strength_classes():
    tsx = read("components/NetworkControl.tsx")
    css = read("style.css")

    assert "function wifiSignalClass" in tsx
    assert "network-trigger-icon" in tsx
    assert "network-connection-icon" in tsx
    assert "network-quick-icon" in tsx
    assert "wifi-signal-offline" in tsx
    for name in ("offline", "weak", "ok", "strong"):
        assert f".wifi-signal-{name}" in css


def test_power_trigger_uses_centered_icon_shell():
    tsx = read("components/PowerControl.tsx")
    css = read("style.css")

    assert "power-trigger-shell" in tsx
    assert "power-trigger-icon" in tsx
    assert "power-trigger-status-dot" in tsx
    shell = css_block(css, ".power-trigger-shell")
    icon = css_block(css, ".power-trigger-icon")
    assert "min-width:" in shell
    assert "min-height:" in shell
    assert "font-size:" in icon


def test_battery_uses_level_classes_and_three_pulse_warning():
    tsx = read("components/Battery.tsx")
    css = read("style.css")

    assert "function batteryLevelClass" in tsx
    assert "battery-level-critical" in tsx
    assert "battery-shell" in tsx
    assert "battery-fill" in tsx
    assert "battery-icon" not in tsx
    assert "battery-percent" in tsx
    assert "battery-v28-warning-pulse" in css
    warning = css_block(css, ".battery-card.battery-level-warning:not(.charging)")
    critical = css_block(css, ".battery-card.battery-level-critical:not(.charging)")
    assert "battery-v28-warning-pulse" in warning
    assert "3" in warning
    assert "battery-v28-warning-pulse" in critical
    assert "3" in critical
