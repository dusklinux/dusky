from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TSX = (ROOT / "components" / "NetworkControl.tsx").read_text()
CSS = (ROOT / "style.css").read_text()


def test_metric_rows_are_not_homogeneous_three_column_layouts():
    assert 'class="network-live-grid" homogeneous' not in TSX
    assert 'class="network-session-breakdown" homogeneous' not in TSX
    assert 'class="network-live-metric download" orientation={Gtk.Orientation.VERTICAL} spacing={2} hexpand' in TSX
    assert 'class="network-live-metric upload" orientation={Gtk.Orientation.VERTICAL} spacing={2} hexpand' in TSX
    assert 'class="network-session-stat" spacing={5} halign={Gtk.Align.CENTER} hexpand' in TSX
    assert TSX.count('class="network-metric-divider"') == 2


def test_network_shell_is_compact_without_reducing_primary_typography():
    assert '.network-dashboard {' in CSS
    assert 'min-width: 295px;' in CSS
    assert 'padding: 9px;' in CSS
    assert '.network-live-grid {' in CSS and 'min-height: 48px;' in CSS
    assert '.network-quick-action {' in CSS and 'min-height: 34px;' in CSS
    assert '.network-connection-name {' in CSS and 'font-size: 17px;' in CSS
    assert '.network-live-value {' in CSS and 'font-size: 15px;' in CSS
    assert '.network-session-total {' in CSS and 'font-size: 17px;' in CSS


def test_network_spacing_is_tighter_and_divider_is_true_one_pixel_column():
    assert 'class="network-dashboard" orientation={Gtk.Orientation.VERTICAL} spacing={6}' in TSX
    assert TSX.count('class="network-section" orientation={Gtk.Orientation.VERTICAL} spacing={3}') == 2
    assert 'class="network-actions" orientation={Gtk.Orientation.VERTICAL} spacing={4}' in TSX
    assert '.network-metric-divider {' in CSS
    assert 'min-width: 1px;' in CSS
    assert 'margin: 5px 3px;' in CSS
