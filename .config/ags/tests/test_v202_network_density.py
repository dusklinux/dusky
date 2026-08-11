from pathlib import Path
import re

ROOT = Path(__file__).resolve().parents[1]
CSS = (ROOT / "style.css").read_text()
TSX = (ROOT / "components" / "NetworkControl.tsx").read_text()


def block(selector: str) -> str:
    m = re.search(rf"{re.escape(selector)}\s*\{{([^}}]*)\}}", CSS, re.S)
    assert m, f"missing CSS selector: {selector}"
    return m.group(1)


def test_network_shell_is_compact():
    b = block(".network-dashboard")
    assert "min-width: 295px" in b
    assert "padding: 9px" in b


def test_network_cards_are_shorter():
    assert "min-height: 48px" in block(".network-live-grid")
    assert "padding: 6px 8px 5px 8px" in block(".network-session-card")
    assert "min-height: 34px" in block(".network-quick-action")


def test_network_typography_is_larger_not_smaller():
    assert "font-size: 17px" in block(".network-connection-name")
    assert "font-size: 11px" in block(".network-connection-status")
    assert "font-size: 15px" in block(".network-live-value")
    assert "font-size: 10px" in block(".network-live-label")
    assert "font-size: 17px" in block(".network-session-total")
    assert "font-size: 12px" in block(".network-session-stat")
    assert "font-size: 10px" in block(".network-session-since")
    assert "font-size: 12px" in block(".network-quick-title")
    assert "font-size: 10px" in block(".network-quick-action-label")


def test_network_runtime_logic_is_not_reworked_for_density_patch():
    assert "networkSession" in TSX
    assert "runNetworkManager" in TSX
    assert "runBluetoothManager" in TSX
