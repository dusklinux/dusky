from pathlib import Path
import re

ROOT = Path(__file__).resolve().parents[1]


def read(rel: str) -> str:
    return (ROOT / rel).read_text()


def css_block(css: str, selector: str) -> str:
    matches = re.findall(r"(?m)^" + re.escape(selector) + r"\s*\{(.*?)\}", css, re.S)
    assert matches, f"missing CSS block for {selector}"
    return matches[-1]


def test_network_session_meter_is_kernel_backed_and_runtime_persistent():
    path = ROOT / "lib/networkSession.ts"
    assert path.exists(), "lib/networkSession.ts must be created"
    ts = path.read_text()
    assert "/proc/net/route" in ts
    assert "/proc/uptime" in ts
    assert "/sys/class/net/" in ts
    assert "statistics" in ts
    assert "rx_bytes" in ts
    assert "tx_bytes" in ts
    assert "GLib.get_user_runtime_dir()" in ts
    assert "dusky-adaptive-glass-network-session.json" in ts
    assert "rxTotal" in ts and "txTotal" in ts
    assert "interfaceName" in ts
    assert "formatBytes" in ts
    assert "formatRate" in ts
    assert "formatSince" in ts
    assert "createPoll" in ts


def test_network_panel_uses_single_connection_identity_and_dashboard_sections():
    ts = read("components/NetworkControl.tsx")
    assert 'class="network-dashboard"' in ts
    assert 'label="LIVE TRAFFIC"' in ts
    assert 'label="SESSION DATA"' in ts
    assert 'class="network-connection-name"' in ts
    assert 'class="network-connection-status"' in ts
    assert 'class="network-session-total"' in ts
    assert 'label="Download"' in ts
    assert 'label="Upload"' in ts
    assert 'title="Wi-Fi"' in ts
    assert 'title="Bluetooth"' in ts
    assert 'actionLabel="Manage"' in ts
    assert 'actionLabel="Devices"' in ts
    assert 'runNetworkManager()' in ts
    assert 'runBluetoothManager()' in ts
    # Old duplicated SSID row pattern must be gone.
    assert 'class="row-subtitle"' not in ts
    assert 'label={createBinding(radio, "ssid")' not in ts


def test_network_panel_uses_proper_symbolic_icons_without_decorative_icon_shells():
    ts = read("components/NetworkControl.tsx")
    assert '<image' in ts
    assert 'network-wireless' in ts
    assert 'go-down-symbolic' in ts
    assert 'go-up-symbolic' in ts
    assert 'bluetooth-active-symbolic' in ts or 'bluetooth-symbolic' in ts
    assert 'go-next-symbolic' in ts
    assert 'network-icon-shell' not in ts
    assert 'metric-icon-shell' not in ts
    assert 'bluetooth-disabled-symbolic' in ts
    assert 'wifiActionIcon' in ts
    assert 'btActionIcon' in ts


def test_network_dashboard_has_compact_dark_dedicated_styling():
    css = read("style.css")
    dashboard = css_block(css, ".network-dashboard")
    width = re.search(r"min-width:\s*(\d+)px", dashboard)
    padding = re.search(r"padding:\s*(\d+)px", dashboard)
    assert width and int(width.group(1)) <= 360
    assert padding and int(padding.group(1)) <= 14
    assert "max-width:" not in dashboard
    popup = css_block(css, ".popup-network")
    assert "background-image" in popup
    assert "0.9" in popup
    assert ".network-live-grid" in css
    assert ".network-session-card" in css
    assert ".network-quick-action" in css
    action = css_block(css, ".network-quick-action")
    assert "min-height" in action
    assert "border-radius" in action


def test_network_dashboard_imports_session_meter_and_keeps_bar_trigger():
    ts = read("components/NetworkControl.tsx")
    assert 'from "../lib/networkSession"' in ts
    assert 'panel="network"' in ts
    assert 'class="control-leader network-leader"' in ts
    assert 'createBinding(network, "wifi", "iconName")' in ts
