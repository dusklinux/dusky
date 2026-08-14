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
    assert '"%I:%M"' in tsx
    assert '"%H:%M"' in tsx
    assert 'format("%p")' in tsx
    assert 'class="clock-card-content"' in tsx
    assert 'ClockReelDigit' in tsx
    assert 'class="clock-reel"' in tsx
    assert 'clock-reel-digit' in tsx
    assert 'clock-reel-old' in tsx
    assert 'clock-reel-new' in tsx
    assert 'class="clock-reel-separator"' in tsx
    assert 'class="clock-accent-dot"' not in tsx
    assert 'class="clock-divider"' in tsx
    assert 'class="clock-meridiem"' in tsx
    assert 'class="clock-label"' not in tsx


def test_clock_pill_is_reference_capsule_with_subtle_motion():
    css = read('style.css')
    card = css_block(css, '.clock-card')
    hover = css_block(css, '.clock-card:hover')
    digit = css_block(css, '.clock-reel-digit')
    face = css_block(css, '.clock-reel-digit-face')
    meridiem = css_block(css, '.clock-meridiem')
    dot = css_block(css, '.clock-accent-dot')

    assert 'min-width: 88px' in card
    assert 'padding: 0 13px' in card
    assert 'border-color: alpha(@primary, 0.28)' in card
    assert 'border-radius: 13px' in card
    assert '0 0 12px alpha(@primary' in card
    assert 'transform: none' in hover
    assert 'animation: none' in digit
    assert 'clock-v28-reel-old' in css
    assert 'clock-v28-reel-new' in css
    assert 'font-size: 13px' in face
    assert 'font-feature-settings: "tnum"' in face
    assert 'font-size: 9px' in meridiem
    assert 'min-width: 0' in dot
    assert 'min-height: 0' in dot
    assert 'opacity: 0' in dot
    assert 'box-shadow: none' in dot


def test_clock_has_frosted_mist_variant_without_white_sheet():
    css = read('style.css')
    assert '.theme-light .clock-card' in css
    light = css_block(css, '.theme-light .clock-card')
    assert 'alpha(#e0e9ed, 0.88)' in light
    assert 'alpha(#607583, 0.19)' in light
