#!/usr/bin/env python3
import json
import sys
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

TABS = ["Networks", "Saved", "Status", "DNS", "Speed Test", "Hotspot"]

SCHEMA = {0: [], 1: [], 2: [], 3: [], 4: [], 5: []}

# ============================================================================
#  Tab 0: Networks (populated from cache for instant startup)
# ============================================================================
SCHEMA[0].append(ConfigItem(
    label="⟳ Rescan Networks",
    key="rescan",
    scope="network",
    type_="bool",
    default=False,
    group="Actions",
    options=["trigger"],
    extended_help="Triggers a new wireless network scan in the background."
))

cache_path = Path.home() / ".cache" / "dusky_tui" / "wifi_cache.json"
if cache_path.exists():
    try:
        with open(cache_path, "r", encoding="utf-8") as f:
            _scans = json.load(f)
        for _net in _scans:
            _ssid = _net.get("ssid", "")
            _signal = _net.get("signal", 0)
            _security = _net.get("security", "Open")
            _in_use = _net.get("in_use", False)

            def _bar(s):
                if s >= 80: return "▂▄▆█"
                if s >= 60: return "▂▄▆_"
                if s >= 40: return "▂▄__"
                if s >= 20: return "▂___"
                return "____"

            _icon = "●" if _in_use else "○"
            _status = "Active" if _in_use else "New"
            _label = f"{_icon} {_status:<6} {_ssid:<24} {_security:<10} {_signal}% {_bar(_signal)}"
            _pkey = f"net__{_ssid}"

            _item = ConfigItem(
                label=_label,
                key=_pkey,
                scope="network",
                type_="menu",
                is_parent=True,
                expanded=False,
                group="Available Networks"
            )
            _item.exists_in_target = True
            _item._initial_loaded = True
            SCHEMA[0].append(_item)
    except Exception:
        pass

if len(SCHEMA[0]) <= 1:
    SCHEMA[0].append(ConfigItem(
        label="  ⟳  Scanning available networks...",
        key="loading_networks",
        scope="network",
        type_="action",
        default=":",
        group="Available Networks"
    ))

# ============================================================================
#  Tab 1: Saved Connections
# ============================================================================
SCHEMA[1].append(ConfigItem(
    label="  ⟳  Loading saved profiles...",
    key="loading_saved",
    scope="saved",
    type_="action",
    default=":",
    group="Saved Connections"
))

# ============================================================================
#  Tab 2: Status & Live Traffic — updated dynamically by engine
# ============================================================================
SCHEMA[2].extend([
    ConfigItem(
        label="Wi-Fi Radio Switch",
        key="wifi_radio",
        scope="status",
        type_="bool",
        default=True,
        group="Hardware Control",
        extended_help="Enable or disable the wireless radio hardware interface."
    ),
    ConfigItem(
        label="Connection:   Disconnected",
        key="status_type",
        scope="clipboard",
        type_="bool",
        default=False,
        options=["copy"],
        group="Connection Information"
    ),
    ConfigItem(
        label="SSID / Name:  None",
        key="status_ssid",
        scope="clipboard",
        type_="bool",
        default=False,
        options=["copy"],
        group="Connection Information"
    ),
    ConfigItem(
        label="IP Address:   N/A",
        key="status_ip",
        scope="clipboard",
        type_="bool",
        default=False,
        options=["copy"],
        group="Connection Information"
    ),
    ConfigItem(
        label="Gateway:      N/A",
        key="status_gateway",
        scope="clipboard",
        type_="bool",
        default=False,
        options=["copy"],
        group="Connection Information"
    ),
    ConfigItem(
        label="Link Detail:  N/A",
        key="status_detail",
        scope="clipboard",
        type_="bool",
        default=False,
        options=["copy"],
        group="Connection Information"
    ),
    ConfigItem(
        label="Interface:    N/A",
        key="status_device",
        scope="clipboard",
        type_="bool",
        default=False,
        options=["copy"],
        group="Connection Information"
    ),
    ConfigItem(
        label="Download Rate: ↓ 0 B/s",
        key="throughput_down",
        scope="clipboard",
        type_="bool",
        default=False,
        options=["copy"],
        group="Live Throughput"
    ),
    ConfigItem(
        label="Upload Rate:   ↑ 0 B/s",
        key="throughput_up",
        scope="clipboard",
        type_="bool",
        default=False,
        options=["copy"],
        group="Live Throughput"
    ),
    ConfigItem(
        label="Total Received: 0 B",
        key="throughput_rx_total",
        scope="clipboard",
        type_="bool",
        default=False,
        options=["copy"],
        group="Live Throughput"
    ),
    ConfigItem(
        label="Total Sent:     0 B",
        key="throughput_tx_total",
        scope="clipboard",
        type_="bool",
        default=False,
        options=["copy"],
        group="Live Throughput"
    ),
    ConfigItem(
        label="Router Gateway Ping: N/A",
        key="ping_router",
        scope="clipboard",
        type_="bool",
        default=False,
        options=["copy"],
        group="Latency & Packet Loss"
    ),
    ConfigItem(
        label="Internet Ping (1.1.1.1): N/A",
        key="ping_internet",
        scope="clipboard",
        type_="bool",
        default=False,
        options=["copy"],
        group="Latency & Packet Loss"
    ),
    ConfigItem(
        label="Packet Loss:         0%",
        key="ping_packet_loss",
        scope="clipboard",
        type_="bool",
        default=False,
        options=["copy"],
        group="Latency & Packet Loss"
    ),
    ConfigItem(
        label="Disconnect Current Connection",
        key="disconnect",
        scope="status_action",
        type_="bool",
        default=False,
        options=["trigger"],
        group="Actions",
        extended_help="Disconnects the current active WiFi network profile."
    ),
    ConfigItem(
        label="Restart NetworkManager Service",
        key="restart_nm",
        scope="status_action",
        type_="bool",
        default=False,
        options=["trigger"],
        group="Actions",
        extended_help="Restarts systemd NetworkManager daemon in case of hangs."
    ),
    ConfigItem(
        label="Force Wireless Interface Rescan",
        key="rescan",
        scope="status_action",
        type_="bool",
        default=False,
        options=["trigger"],
        group="Actions",
        extended_help="Forces NetworkManager to perform an immediate rescan."
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
    from rich.columns import Columns
    from rich.align import Align
    from python.engines.network_manager import (
        NetworkManagerEngine,
        parse_key_value,
        format_rate,
        format_bytes,
        format_ping_latency,
        format_packet_loss
    )

    verb = {}
    tp = {}
    ping = {}
    dns_provider = "DHCP"

    eng = getattr(NetworkManagerEngine, "_instance", None)

    if not eng and app and hasattr(app, "engine_pool"):
        for e in list(app.engine_pool.values()):
            if isinstance(e, NetworkManagerEngine):
                eng = e
                break

    if eng:
        verb = getattr(eng, "_verbose_info", {})
        if not verb:
            try:
                raw = eng._run_cmd([eng._find_script("omarchy-network-status"), "--verbose"], timeout=3)
                v_info = parse_key_value(raw)
                act = eng._get_active_wifi_connection()
                verb = eng._enrich_network_status(v_info, act)
                eng._verbose_info = verb
            except Exception:
                pass

        tp = getattr(eng, "_tp_state", {})
        ping = getattr(eng, "_ping_state", {})
        dns_provider = getattr(eng, "_dns_provider", "DHCP")

    # Connection details
    conn_type = verb.get("type", "wifi").upper()
    ssid = verb.get("ssid", "None")
    ip = verb.get("ip", "N/A")
    prefix = verb.get("prefix", "")
    if ip != "N/A" and prefix:
        ip = f"{ip}/{prefix}"
    gw = verb.get("gateway", "N/A")
    iface = verb.get("iface", "N/A")
    phy_iface = verb.get("phy_iface", "")
    if phy_iface and phy_iface != iface:
        iface_str = f"{iface} ({phy_iface})"
    else:
        iface_str = iface

    freq = verb.get("freq", "")
    bitrate = verb.get("bitrate", "")
    link_detail = (f"{freq} MHz" if freq else "N/A") + (f" ({bitrate})" if bitrate else "")

    # Icons
    is_wifi = conn_type == "WIFI"
    conn_icon = "󰤨" if is_wifi else "󰈀"

    # Throughput
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

    # Pings
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
    packet_loss = format_packet_loss(ping.get("internet_ping_packet_loss", 0))

    # Panel 1: Connection Info
    t_conn = Table(show_header=False, box=None, padding=(0, 1))
    t_conn.add_column(style="dim", justify="right")
    t_conn.add_column(style="bold white", justify="left")
    t_conn.add_row("Connection:", f"{conn_icon} {conn_type} ({ssid})")
    t_conn.add_row("SSID / Name:", ssid)
    t_conn.add_row("IP Address:", ip)
    t_conn.add_row("Gateway:", gw)
    t_conn.add_row("Interface:", iface_str)
    t_conn.add_row("Link Detail:", link_detail)
    p_conn = Panel(t_conn, title="[bold cyan] 📡 CONNECTION INFORMATION [/bold cyan]", border_style="cyan", expand=True)

    # Panel 2: Live Throughput
    t_tp = Table(show_header=False, box=None, padding=(0, 1))
    t_tp.add_column(style="dim", justify="right")
    t_tp.add_column(style="bold green", justify="left")
    t_tp.add_row("Download Rate:", f"↓ {dl_rate}")
    t_tp.add_row("Upload Rate:", f"↑ {ul_rate}")
    t_tp.add_row("Total Received:", rx_total)
    t_tp.add_row("Total Sent:", tx_total)
    p_tp = Panel(t_tp, title="[bold green] 📊 LIVE THROUGHPUT [/bold green]", border_style="green", expand=True)

    # Panel 3: Latency & Security
    t_ping = Table(show_header=False, box=None, padding=(0, 1))
    t_ping.add_column(style="dim", justify="right")
    t_ping.add_column(style="bold yellow", justify="left")
    t_ping.add_row("Router Gateway Ping:", router_ping)
    t_ping.add_row("Internet Ping (1.1.1.1):", internet_ping)
    t_ping.add_row("Packet Loss:", packet_loss)
    t_ping.add_row("Active DNS Provider:", dns_provider)
    p_ping = Panel(t_ping, title="[bold yellow] ⚡ LATENCY & METRICS [/bold yellow]", border_style="yellow", expand=True)

    right_group = Group(p_tp, p_ping)

    grid = Table.grid(expand=True)
    grid.add_column(ratio=1)
    grid.add_column(ratio=1)
    grid.add_row(p_conn, right_group)

    footer = Text(" Switch tabs using [Tab] / [Shift+Tab] or [Alt+1..6] (Networks, Saved, Status, DNS, Speed Test, Hotspot)", style="dim italic")

    return Group(grid, Align.center(footer))


CUSTOM_VIEWS = {
    2: {
        "view": render_network_dashboard_view,
        "interval": 1.0
    }
}

# ============================================================================
#  Tab 3: DNS — DNS Provider Switching
# ============================================================================
SCHEMA[3].extend([
    ConfigItem(
        label="Current DNS Provider: DHCP",
        key="dns_current",
        scope="dns_info",
        type_="action",
        default=":",
        group="DNS Information"
    ),
    ConfigItem(
        label="▶ Switch to DHCP (Automatic)",
        key="dns_dhcp",
        scope="dns_action",
        type_="bool",
        default=False,
        options=["trigger"],
        group="Provider Selection",
        extended_help="Resets DNS configuration to automatic DHCP from router."
    ),
    ConfigItem(
        label="▶ Switch to Cloudflare (1.1.1.1)",
        key="dns_cloudflare",
        scope="dns_action",
        type_="bool",
        default=False,
        options=["trigger"],
        group="Provider Selection",
        extended_help="Uses Cloudflare fast & private DNS (1.1.1.1 / 1.0.0.1)."
    ),
    ConfigItem(
        label="▶ Switch to Google (8.8.8.8)",
        key="dns_google",
        scope="dns_action",
        type_="bool",
        default=False,
        options=["trigger"],
        group="Provider Selection",
        extended_help="Uses Google Public DNS (8.8.8.8 / 8.8.4.4)."
    ),
    ConfigItem(
        label="▶ Switch to Custom DNS Servers",
        key="dns_custom",
        scope="dns_action",
        type_="string",
        default="",
        group="Provider Selection",
        extended_help="Enter custom space/comma-separated IP addresses (e.g. 9.9.9.9 1.1.1.1)."
    ),
])

# ============================================================================
#  Tab 4: Speed Test — Fast.com speed test integration
# ============================================================================
SCHEMA[4].extend([
    ConfigItem(
        label="▶ Run Full Speed Test (Download + Upload)",
        key="speedtest_full",
        scope="speedtest_action",
        type_="bool",
        default=False,
        options=["trigger"],
        group="Run Test",
        extended_help="Spawns fast.com download & upload speed test workers."
    ),
    ConfigItem(
        label="▶ Run Download Test Only",
        key="speedtest_down",
        scope="speedtest_action",
        type_="bool",
        default=False,
        options=["trigger"],
        group="Run Test"
    ),
    ConfigItem(
        label="▶ Run Upload Test Only",
        key="speedtest_up",
        scope="speedtest_action",
        type_="bool",
        default=False,
        options=["trigger"],
        group="Run Test"
    ),
    ConfigItem(
        label="Status: Ready",
        key="speedtest_status",
        scope="speedtest_info",
        type_="action",
        default=":",
        group="Test Status & Results"
    ),
    ConfigItem(
        label="Download Speed: --",
        key="speedtest_down_result",
        scope="clipboard",
        type_="bool",
        default=False,
        options=["copy"],
        group="Test Status & Results"
    ),
    ConfigItem(
        label="Upload Speed:   --",
        key="speedtest_up_result",
        scope="clipboard",
        type_="bool",
        default=False,
        options=["copy"],
        group="Test Status & Results"
    ),
])

# ============================================================================
#  Tab 5: Hotspot
# ============================================================================
SCHEMA[5].extend([
    ConfigItem(
        label="Hotspot SSID",
        key="hotspot_ssid",
        scope="hotspot",
        type_="string",
        default="MyHotspot",
        group="Hotspot Configuration",
        extended_help="Set the SSID/Name for the broadcasted Wi-Fi Hotspot."
    ),
    ConfigItem(
        label="Hotspot Password",
        key="hotspot_password",
        scope="hotspot",
        type_="string",
        default="",
        group="Hotspot Configuration",
        extended_help="Password for the hotspot (minimum 8 characters). Leave empty for an open network."
    ),
    ConfigItem(
        label="Start Hotspot (2.4 GHz)",
        key="start_hotspot_24",
        scope="hotspot",
        type_="bool",
        default=False,
        options=["trigger"],
        group="Hotspot Actions",
        extended_help="Broadcasts a 2.4 GHz Access Point using the configured SSID and Password."
    ),
    ConfigItem(
        label="Start Hotspot (5 GHz)",
        key="start_hotspot_5",
        scope="hotspot",
        type_="bool",
        default=False,
        options=["trigger"],
        group="Hotspot Actions",
        extended_help="Broadcasts a 5 GHz Access Point using the configured SSID and Password."
    ),
    ConfigItem(
        label="Stop Hotspot",
        key="stop_hotspot",
        scope="hotspot",
        type_="bool",
        default=False,
        options=["trigger"],
        group="Hotspot Actions",
        extended_help="Stops the active broadcast on the wireless adapter."
    ),
    ConfigItem(
        label="Status: Inactive",
        key="hotspot_status_info",
        scope="hotspot",
        type_="action",
        default=":",
        group="Hotspot Status"
    ),
    ConfigItem(
        label="Connected Clients: N/A",
        key="hotspot_clients_info",
        scope="hotspot",
        type_="action",
        default=":",
        group="Hotspot Status"
    )
])

# =============================================================================
# DIRECT EXECUTION HANDLER
# =============================================================================
if __name__ == "__main__":
    import subprocess
    from pathlib import Path

    script_path = Path(__file__).resolve()
    main_router = Path.home() / "user_scripts" / "dusky_tui" / "python" / "main" / "main.py"

    if main_router.exists():
        sys.exit(subprocess.run([sys.executable, str(main_router), str(script_path)] + sys.argv[1:]).returncode)
    else:
        print(f"[-] Error: Main Dusky TUI router not found at {main_router}", file=sys.stderr)
        sys.exit(1)
