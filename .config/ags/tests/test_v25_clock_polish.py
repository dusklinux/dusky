from pathlib import Path
import re

ROOT = Path(__file__).resolve().parents[1]


def read(rel: str) -> str:
    return (ROOT / rel).read_text()


def css_block(css: str, selector: str) -> str:
    matches = re.findall(r"(?m)^" + re.escape(selector) + r"\s*\{(.*?)\}", css, re.S)
    assert matches, f"missing CSS block for {selector}"
    return matches[-1]


def test_clock_pill_has_structured_time_and_meridiem_not_plain_single_label():
    tsx = read('components/ClockCard.tsx')
    assert 'format("%I:%M")' in tsx
    assert 'format("%p")' in tsx
    assert 'class="clock-card-content"' in tsx
    assert 'class="clock-accent-dot"' in tsx
    assert 'class="clock-time"' in tsx
    assert 'class="clock-divider"' in tsx
    assert 'class="clock-meridiem"' in tsx
    assert 'class="clock-label"' not in tsx


def test_clock_pill_is_restrained_center_anchor_with_subtle_motion():
    css = read('style.css')
    card = css_block(css, '.clock-card')
    hover = css_block(css, '.clock-card:hover')
    time = css_block(css, '.clock-time')
    meridiem = css_block(css, '.clock-meridiem')
    dot = css_block(css, '.clock-accent-dot')

    assert 'min-width: 0' in card
    assert 'padding: 0 8px' in card
    assert 'border-color: alpha(@outline_variant, 0.22)' in card
    assert 'transition: 180ms ease' in card
    assert '0 0 10px alpha(@primary' not in card
    assert 'transform: none' in hover
    assert 'font-size: 13px' in time
    assert 'font-size: 9px' in meridiem
    assert 'min-width: 6px' in dot
    assert 'min-height: 6px' in dot


def test_clock_has_frosted_mist_variant_without_white_sheet():
    css = read('style.css')
    assert '.theme-light .clock-card' in css
    light = css_block(css, '.theme-light .clock-card')
    assert 'alpha(#dce6ea, 0.86)' in light
    assert 'alpha(#607583, 0.20)' in light
