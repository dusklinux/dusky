#!/usr/bin/env python3
import sys
import subprocess
from pathlib import Path

_DUSKY_TUI_ROOT = Path.home() / "user_scripts" / "dusky_tui"
if str(_DUSKY_TUI_ROOT) not in sys.path:
    sys.path.insert(0, str(_DUSKY_TUI_ROOT))

from python.frontend.core_types import ConfigItem

ENGINE_TYPE = "network"
TARGET_FILE = "~/.cache/dusky_tui/wifi_cache.json"
APP_TITLE = "Dusky Network Manager"
DEFAULT_MODE = "auto"
THEME_FILE = "~/.config/matugen/generated/dusky_tui.json"
ENABLE_USER_PRESETS = False

TABS = ["Networks", "Saved", "Status", "Devices", "Speed Test", "Hotspot"]

SCHEMA = {0: [], 1: [], 2: [], 3: [], 4: [], 5: []}

# ============================================================================
#  Tab 0: Networks (the engine owns discovery and its cache)
# ============================================================================
SCHEMA[0] = [
    ConfigItem(label="Wi-Fi Radio", key="wifi_radio", scope="status", type_="bool", default=True,
               group="Hardware", extended_help="Toggle Wi-Fi radio on/off."),
    ConfigItem(label="Rescan", key="rescan", scope="network", type_="bool", default=False,
               group="Actions", options=["trigger"], extended_help="Scan for nearby Wi-Fi networks."),
    ConfigItem(label="Hidden Network…", key="hidden_menu", scope="network", type_="menu", default=None,
               is_parent=True, expanded=False, group="Actions",
               extended_help="Connect to a hidden Wi-Fi network that does not broadcast its SSID."),
    ConfigItem(label="Hidden SSID", key="hidden_ssid", scope="network", type_="string", default="",
               parent_ref="network.hidden_menu", group="Actions", extended_help="SSID of the hidden network."),
    ConfigItem(label="Password", key="hidden_password", scope="network", type_="string", default="",
               parent_ref="network.hidden_menu", group="Actions",
               extended_help="Password for the hidden network. Leave blank for open networks."),
    ConfigItem(label="Security", key="hidden_security", scope="network", type_="cycle", default="Auto",
               options=["Auto", "WPA/WPA2/WPA3", "WPA3 (SAE)", "Open", "WEP"], parent_ref="network.hidden_menu",
               group="Actions", extended_help="Security protocol. Auto will probe the access point."),
    ConfigItem(label="Connect", key="connect_hidden", scope="network", type_="bool", default=False,
               options=["trigger"], parent_ref="network.hidden_menu", group="Actions",
               extended_help="Connect to the specified hidden Wi-Fi network."),
    ConfigItem(label="Share QR", key="qr_hidden", scope="network", type_="bool", default=False,
               options=["trigger"], parent_ref="network.hidden_menu", group="Actions",
               extended_help="Show Wi-Fi QR code for the specified hidden network."),
    ConfigItem(label="Loading networks…", key="loading_networks", scope="network", type_="action",
               default=":", read_only=True, group="Networks"),
]

# ============================================================================
#  Tab 1: Saved Connections
# ============================================================================
SCHEMA[1].append(ConfigItem(
    label="Loading...",
    key="loading_saved",
    scope="saved",
    type_="action",
    default=":", read_only=True,
    group="Saved"
))

# ============================================================================
#  Tab 2: Status & Live Traffic — updated dynamically by engine
# ============================================================================
SCHEMA[2].extend([
    ConfigItem(
        label="Wi-Fi Radio",
        key="wifi_radio",
        scope="status",
        type_="bool",
        default=True,
        group="Hardware",
        extended_help="Toggle Wi-Fi radio on/off."
    ),
    ConfigItem(
        label="Connection:   Disconnected",
        key="status_type",
        scope="clipboard",
        type_="bool",
        default=False,
        options=["copy"],
        group="Info"
    ),
    ConfigItem(
        label="SSID:  None",
        key="status_ssid",
        scope="clipboard",
        type_="bool",
        default=False,
        options=["copy"],
        group="Info"
    ),
    ConfigItem(
        label="IP:   N/A",
        key="status_ip",
        scope="clipboard",
        type_="bool",
        default=False,
        options=["copy"],
        group="Info"
    ),
    ConfigItem(
        label="Gateway:      N/A",
        key="status_gateway",
        scope="clipboard",
        type_="bool",
        default=False,
        options=["copy"],
        group="Info"
    ),
    ConfigItem(
        label="Link:  N/A",
        key="status_detail",
        scope="clipboard",
        type_="bool",
        default=False,
        options=["copy"],
        group="Info"
    ),
    ConfigItem(
        label="Iface:    N/A",
        key="status_device",
        scope="clipboard",
        type_="bool",
        default=False,
        options=["copy"],
        group="Info"
    ),
    ConfigItem(
        label="Down: ↓ 0 B/s",
        key="throughput_down",
        scope="clipboard",
        type_="bool",
        default=False,
        options=["copy"],
        group="Throughput"
    ),
    ConfigItem(
        label="Up:   ↑ 0 B/s",
        key="throughput_up",
        scope="clipboard",
        type_="bool",
        default=False,
        options=["copy"],
        group="Throughput"
    ),
    ConfigItem(
        label="Down Total: ↓ 0 B",
        key="throughput_rx_total",
        scope="clipboard",
        type_="bool",
        default=False,
        options=["copy"],
        group="Throughput",
        extended_help="Received bytes since this interface was initialized, including previous connections. This is not a per-connection total."
    ),
    ConfigItem(
        label="Up Total: ↑ 0 B",
        key="throughput_tx_total",
        scope="clipboard",
        type_="bool",
        default=False,
        options=["copy"],
        group="Throughput",
        extended_help="Transmitted bytes since this interface was initialized, including previous connections. This is not a per-connection total."
    ),
    ConfigItem(
        label="Router Ping: N/A",
        key="ping_router",
        scope="clipboard",
        type_="bool",
        default=False,
        options=["copy"],
        group="Latency",
        extended_help="Average successful ICMP replies among the last five gateway probes on the displayed interface. A blocked ICMP reply does not establish a connectivity failure."
    ),
    ConfigItem(
        label="Internet Ping: N/A",
        key="ping_internet",
        scope="clipboard",
        type_="bool",
        default=False,
        options=["copy"],
        group="Latency",
        extended_help="Average successful ICMP replies among the last five probes to Cloudflare on the displayed interface. ICMP failure alone does not mean the internet is unavailable."
    ),
    ConfigItem(
        label="Loss: N/A",
        key="ping_packet_loss",
        scope="clipboard",
        type_="bool",
        default=False,
        options=["copy"],
        group="Latency",
        extended_help="Failed internet ICMP probes among the last 24 attempts. N/A means no probe was attempted."
    ),
    ConfigItem(
        label="DNS: DHCP",
        key="dns_current",
        scope="clipboard",
        type_="bool",
        default=False,
        options=["copy"],
        group="Latency",
        extended_help="Active DNS provider."
    ),
    ConfigItem(
        label="Restart NM",
        key="restart_nm",
        scope="status_action",
        type_="bool",
        default=False,
        options=["trigger"],
        group="Actions",
        extended_help="Restart NetworkManager."
    ),
    ConfigItem(
        label="Force Rescan",
        key="rescan",
        scope="status_action",
        type_="bool",
        default=False,
        options=["trigger"],
        group="Actions",
        extended_help="Force Wi-Fi rescan."
    )
])

# ============================================================================
#  CUSTOM RICH VIEW FOR TAB 2 (Status / Live Metrics Dashboard)
# ============================================================================
def render_network_dashboard_view(app):
    from rich.panel import Panel
    from rich.table import Table
    from rich.text import Text
    from rich.console import Group
    from python.engines.network_manager import (
        NetworkManagerEngine,
        format_rate,
        format_bytes,
        format_ping_latency,
        format_packet_loss,
        format_header_speed,
    )

    verb = {}
    tp = {}
    ping = {}
    dns_provider = "DHCP"

    eng = None

    if app and hasattr(app, "engine_pool"):
        for e in list(app.engine_pool.values()):
            if isinstance(e, NetworkManagerEngine):
                eng = e
                break
    if eng is None:
        eng = getattr(NetworkManagerEngine, "_instance", None)

    if eng:
        verb = dict(getattr(eng, "_verbose_info", {}))
        tp = dict(getattr(eng, "_tp_state", {}))
        ping = dict(getattr(eng, "_ping_state", {}))
        dns_provider = getattr(eng, "_dns_provider", "DHCP")

    conn_type = verb.get("type", "disconnected").upper()
    ssid = verb.get("ssid", "None")
    ip = verb.get("ip", "N/A")
    prefix = verb.get("prefix", "")
    if ip != "N/A" and prefix:
        ip = f"{ip}/{prefix}"
    gw = verb.get("gateway", "N/A")
    iface = verb.get("iface", "N/A")
    phy_iface = verb.get("phy_iface", "")
    if phy_iface and phy_iface != iface and iface != "N/A":
        iface_str = f"{iface} ({phy_iface})"
    else:
        iface_str = iface

    freq = verb.get("freq", "")
    bitrate = verb.get("bitrate", "")
    if conn_type == "ETHERNET":
        link_detail = format_header_speed(verb.get("speed", "")) or "N/A"
    else:
        link_detail = (f"{freq} MHz" if freq else "N/A") + (f" ({bitrate})" if bitrate else "")

    is_wifi = conn_type == "WIFI"
    conn_icon = "󰤨" if is_wifi else "󰈀"

    dl_rate_val = tp.get("download_rate", 0)
    ul_rate_val = tp.get("upload_rate", 0)

    rx_raw = tp.get("total_rx")
    if rx_raw is None or rx_raw == 0:
        try: rx_raw = int(verb.get("rx_bytes", 0))
        except ValueError: rx_raw = 0

    tx_raw = tp.get("total_tx")
    if tx_raw is None or tx_raw == 0:
        try: tx_raw = int(verb.get("tx_bytes", 0))
        except ValueError: tx_raw = 0

    dl_rate = format_rate(dl_rate_val)
    ul_rate = format_rate(ul_rate_val)
    rx_total = format_bytes(rx_raw)
    tx_total = format_bytes(tx_raw)

    r_lat = ping.get("router_ping_latency")
    if r_lat is None and verb.get("router_ping_ms"):
        try: r_lat = float(verb["router_ping_ms"])
        except ValueError: pass

    i_lat = ping.get("internet_ping_latency")
    if i_lat is None and verb.get("internet_ping_ms"):
        try: i_lat = float(verb["internet_ping_ms"])
        except ValueError: pass

    router_ping = format_ping_latency(r_lat)
    internet_ping = format_ping_latency(i_lat)
    packet_loss = format_packet_loss(ping.get("internet_ping_packet_loss"))

    t_conn = Table(show_header=False, box=None, padding=(0, 1))
    t_conn.add_column(style="dim", justify="right")
    t_conn.add_column(style="bold white", justify="left")
    t_conn.add_row("Connection:", Text(f"{conn_icon} {conn_type} ({ssid})"))
    t_conn.add_row("SSID:" if is_wifi else "Source:", Text(ssid))
    t_conn.add_row("IP:", ip)
    t_conn.add_row("Gateway:", gw)
    t_conn.add_row("Iface:", iface_str)
    t_conn.add_row("Link:", link_detail)
    p_conn = Panel(t_conn, title="[bold cyan] 󰤨 CONNECTION [/bold cyan]", border_style="cyan", expand=True)

    t_tp = Table(show_header=False, box=None, padding=(0, 1))
    t_tp.add_column(style="dim", justify="right")
    t_tp.add_column(style="bold green", justify="left")
    t_tp.add_row("Down:", f"↓ {dl_rate}")
    t_tp.add_row("Up:", f"↑ {ul_rate}")
    t_tp.add_row("Interface RX:", f"↓ {rx_total}")
    t_tp.add_row("Interface TX:", f"↑ {tx_total}")
    p_tp = Panel(t_tp, title="[bold green] 󰓅 THROUGHPUT [/bold green]", border_style="green", expand=True)

    t_ping = Table(show_header=False, box=None, padding=(0, 1))
    t_ping.add_column(style="dim", justify="right")
    t_ping.add_column(style="bold yellow", justify="left")
    t_ping.add_row("Router Ping:", router_ping)
    t_ping.add_row("Internet Ping:", internet_ping)
    t_ping.add_row("Loss:", packet_loss)
    t_ping.add_row("DNS:", dns_provider)
    p_ping = Panel(t_ping, title="[bold yellow] 󰛳 LATENCY [/bold yellow]", border_style="yellow", expand=True)

    right_group = Group(p_tp, p_ping)

    grid = Table.grid(expand=True)
    grid.add_column(ratio=1)
    grid.add_column(ratio=1)
    grid.add_row(p_conn, right_group)

    return grid


CUSTOM_VIEWS = {
    2: {
        "view": render_network_dashboard_view,
        "interval": 1.0,
        "show_options": True,
        "option_groups": {"Hardware", "Actions"},
    }
}

# ============================================================================
#  Tab 3 (index 3): Devices and internet source — populated by engine
# ============================================================================
SCHEMA[3].extend([
    ConfigItem(
        label="Loading...",
        key="loading_devices",
        scope="devices",
        type_="action",
        default=":", read_only=True,
        group="Devices"
    )
])

# ============================================================================
#  Tab 4: Speed Test — installed helper or native Cloudflare measurements
# ============================================================================
SCHEMA[4].extend([
    ConfigItem(
        label="Run All",
        key="speedtest_full",
        scope="speedtest_action",
        type_="bool",
        default=False,
        options=["trigger"],
        group="Run",
        extended_help="Run download & upload test."
    ),
    ConfigItem(
        label="Download",
        key="speedtest_down",
        scope="speedtest_action",
        type_="bool",
        default=False,
        options=["trigger"],
        group="Run"
    ),
    ConfigItem(
        label="Upload",
        key="speedtest_up",
        scope="speedtest_action",
        type_="bool",
        default=False,
        options=["trigger"],
        group="Run"
    ),
    ConfigItem(
        label="Status: Ready",
        key="speedtest_status",
        scope="speedtest_info",
        type_="action",
        default=":", read_only=True,
        group="Results"
    ),
    ConfigItem(
        label="Down: --",
        key="speedtest_down_result",
        scope="clipboard",
        type_="bool",
        default=False,
        options=["copy"],
        group="Results"
    ),
    ConfigItem(
        label="Up: --",
        key="speedtest_up_result",
        scope="clipboard",
        type_="bool",
        default=False,
        options=["copy"],
        group="Results"
    ),
])

# ============================================================================
#  Tab 5: Hotspot
# ============================================================================
SCHEMA[5].extend([
    ConfigItem(
        label="Adapter: Auto",
        key="hotspot_device",
        scope="hotspot",
        type_="cycle",
        default="Auto",
        options=["Auto"],
        group="Config",
        extended_help="Auto prefers an idle Wi-Fi adapter. Using a connected adapter replaces its Wi-Fi connection."
    ),
    ConfigItem(
        label="SSID",
        key="hotspot_ssid",
        scope="hotspot",
        type_="string",
        default="MyHotspot",
        group="Config",
        extended_help="Hotspot name draft. Start saves and applies it; an active hotspot keeps its existing credentials until then."
    ),
    ConfigItem(
        label="Password",
        key="hotspot_password",
        scope="hotspot",
        type_="string",
        default="",
        group="Config",
        extended_help="Password draft: 8–63 printable ASCII characters. Start saves and applies it; blank generates a password. QR sharing uses the active saved profile."
    ),
    ConfigItem(
        label="Start 2.4 GHz",
        key="start_hotspot_24",
        scope="hotspot",
        type_="bool",
        default=False,
        options=["trigger"],
        group="Actions",
        extended_help="Start a local Wi-Fi network, even without internet. A connected adapter will leave its Wi-Fi network."
    ),
    ConfigItem(
        label="Start 5 GHz",
        key="start_hotspot_5",
        scope="hotspot",
        type_="bool",
        default=False,
        options=["trigger"],
        group="Actions",
        extended_help="Start a local Wi-Fi network, even without internet. A connected adapter will leave its Wi-Fi network."
    ),
    ConfigItem(
        label="Stop",
        key="stop_hotspot",
        scope="hotspot",
        type_="bool",
        default=False,
        options=["trigger"],
        group="Actions",
        extended_help="Stop hotspot."
    ),
    ConfigItem(
        label="Share QR",
        key="qr_hotspot",
        scope="hotspot",
        type_="bool",
        default=False,
        options=["trigger"],
        group="Actions",
        extended_help="Show QR for hotspot."
    ),
    ConfigItem(
        label="Status: Inactive",
        key="hotspot_status_info",
        scope="hotspot",
        type_="action",
        default=":", read_only=True,
        group="Status"
    ),
    ConfigItem(
        label="Clients: N/A",
        key="hotspot_clients_info",
        scope="hotspot",
        type_="action",
        default=":", read_only=True,
        group="Status"
    ),
    ConfigItem(
        label="Laptop IP: N/A",
        key="hotspot_address_info",
        scope="hotspot",
        type_="action",
        default=":", read_only=True,
        group="Status",
        extended_help="Use this address from the connected phone for SSH, FTP, or other local services."
    )
])

# =============================================================================
# DIRECT EXECUTION HANDLER
# =============================================================================
if __name__ == "__main__":
    script_path = Path(__file__).resolve()
    main_router = _DUSKY_TUI_ROOT / "python" / "main" / "main.py"

    if main_router.exists():
        sys.exit(subprocess.run([sys.executable, str(main_router), str(script_path)] + sys.argv[1:]).returncode)
    else:
        print(f"[-] Error: Main Dusky TUI router not found at {main_router}", file=sys.stderr)
        sys.exit(1)
