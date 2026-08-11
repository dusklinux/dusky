from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def read(path: str) -> str:
    return (ROOT / path).read_text()


def test_power_panel_uses_native_battery_and_power_profiles():
    text = read("components/PowerControl.tsx")
    assert 'gi://AstalBattery' in text
    assert 'gi://AstalPowerProfiles' in text
    assert 'createBinding' in text
    assert 'percentage' in text
    assert 'activeProfile' in text or 'active-profile' in text


def test_power_panel_has_compact_battery_status_and_time_copy():
    text = read("components/PowerControl.tsx")
    assert 'label="POWER"' in text
    assert 'power-battery-card' in text
    assert 'power-battery-percent' in text
    assert 'power-battery-status' in text
    assert 'formatBatteryTime' in text


def test_power_profiles_are_availability_aware_and_reactive():
    text = read("components/PowerControl.tsx")
    assert 'get_profiles()' in text
    for profile in ('power-saver', 'balanced', 'performance'):
        assert profile in text
    assert 'set_active_profile' in text or 'activeProfile =' in text
    assert 'power-profile-segments' in text


def test_caffeine_state_is_dynamic_and_maps_to_hypridle():
    text = read("lib/powerState.ts")
    assert 'createPoll' in text
    assert 'pgrep -x hypridle' in text
    assert 'toggleIdle' in text
    assert 'caffeineState' in text
    assert 'toggleCaffeine' in text


def test_power_panel_uses_dynamic_caffeine_row_and_trigger_dot():
    text = read("components/PowerControl.tsx")
    assert 'Caffeine' in text
    assert 'Stay awake' in text
    assert 'Idle protection active' in text
    assert 'power-caffeine-row' in text
    assert 'power-caffeine-dot' in text
    assert 'caffeineState' in text


def test_power_actions_include_safe_session_commands_and_confirmation():
    panel = read("components/PowerControl.tsx")
    dusky = read("lib/dusky.ts")
    for label in ('Lock', 'Sleep', 'Logout', 'Soft reboot', 'Reboot', 'Power off'):
        assert f'label="{label}"' in panel or f'>{label}<' in panel
    assert 'createState' in panel
    assert 'power-confirm' in panel
    assert 'Cancel' in panel
    assert 'suspendSession' in dusky
    assert 'restartSession' in dusky
    assert 'shutdownSession' in dusky
    assert 'systemctl suspend' in dusky
    assert 'dusky_session.sh' in dusky
    assert 'softRebootSession' in dusky


def test_old_power_grid_is_removed():
    text = read("components/PowerControl.tsx")
    assert 'power-grid' not in text
    assert 'PowerAction' not in text
    assert 'label="Idle"' not in text
    assert 'power-session-row' not in text


def test_power_css_is_compact_riced_and_has_light_dark_states():
    css = read("style.css")
    assert 'v2.3.1 — Riced Power Deck' in css
    for selector in (
        '.power-panel',
        '.power-battery-card',
        '.power-profile-segments',
        '.power-profile-segment',
        '.power-caffeine-row',
        '.power-caffeine-switch',
        '.power-command-tile',
        '.power-confirm',
        '.theme-light .power-battery-card',
        '.theme-light .power-caffeine-row',
    ):
        assert selector in css
    assert 'min-width: 300px' in css or 'min-width: 298px' in css or 'min-width: 296px' in css
    assert '.power-panel { min-width: 360px; }' not in css
