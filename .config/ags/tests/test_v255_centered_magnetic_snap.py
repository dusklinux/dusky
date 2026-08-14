from pathlib import Path
import re

ROOT = Path(__file__).resolve().parents[1]
CSS = (ROOT / "style.css").read_text()


def snap_block() -> str:
    start = CSS.index("@keyframes workspace-overlay-magnetic-snap")
    end = CSS.index(".workspace-button {", start)
    return CSS[start:end]


def expanded_block() -> str:
    matches = re.findall(r"\.workspace-button\.expanded\s*\{([^}]*)\}", CSS, re.S)
    assert matches
    return matches[-1]


def test_snap_overshoot_is_paint_time_horizontal_not_layout_padding():
    block = snap_block()
    assert "padding:" not in block
    assert "scaleX(" in block
    assert "translateY" not in block
    assert "scaleY" not in block


def test_centered_snap_has_visible_peak_recoil_and_exact_rest():
    block = snap_block()
    scales = [float(v) for v in re.findall(r"scaleX\((\d+(?:\.\d+)?)\)", block)]
    assert scales, "expected horizontal scale keyframes"
    assert max(scales) >= 1.15, scales
    assert any(0.94 <= v <= 0.99 for v in scales[1:-1]), scales
    assert abs(scales[-1] - 1.0) < 1e-9, scales


def test_resting_elastic_width_is_unchanged_from_v254():
    block = expanded_block()
    assert "padding: 0 5px" in block


def test_snapback_is_slower_than_v254():
    matches = re.findall(r"\.workspace-magnetic-shell\.snapping\s*\{([^}]*)\}", CSS, re.S)
    assert matches
    match = next((re.search(r"animation:\s*workspace-overlay-magnetic-snap\s+(\d+)ms", block) for block in matches if "animation:" in block), None)
    assert match
    duration = int(match.group(1))
    assert duration >= 510, duration


def test_lighting_still_peaks_during_snap():
    block = snap_block()
    assert block.count("box-shadow:") >= 3
    assert "alpha(@secondary" in block or "alpha(@primary" in block
