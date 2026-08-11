from pathlib import Path
import re

ROOT = Path(__file__).resolve().parents[1]
POWER = (ROOT / 'components' / 'PowerControl.tsx').read_text()
CSS = (ROOT / 'style.css').read_text()


def block(selector: str) -> str:
    m = re.search(re.escape(selector) + r'\s*\{(.*?)\}', CSS, re.S)
    assert m, f'missing {selector}'
    return m.group(1)


def test_power_mode_is_segmented_not_slider():
    assert 'class="power-profile-rail"' not in POWER
    assert '<slider' not in POWER or 'power-profile-rail' not in POWER
    assert 'class="power-profile-segments"' in POWER
    assert 'power-profile-segment' in POWER
    assert 'set_active_profile(profile)' in POWER


def test_segment_active_state_is_reactive_and_not_duplicated():
    assert 'power-profile-current' not in POWER
    assert 'power-profile-labels' not in POWER
    assert 'activeProfile((active)' in POWER
    assert 'String(active) === profile ? "active" : ""' in POWER


def test_caffeine_switch_is_compact():
    body = block('.power-caffeine-switch')
    assert 'min-width: 34px' in body
    assert 'min-height: 18px' in body
    slider = block('.power-caffeine-switch slider')
    assert 'min-width: 14px' in slider
    assert 'min-height: 14px' in slider


def test_segmented_selector_has_sophisticated_states_in_both_themes():
    for selector in [
        '.power-profile-segments',
        '.power-profile-segment',
        '.power-profile-segment:hover',
        '.power-profile-segment.active',
        '.theme-light .power-profile-segments',
        '.theme-light .power-profile-segment.active',
    ]:
        assert selector in CSS
