#!/usr/bin/env python3
import json
from pathlib import Path
import sys
from pathlib import Path

_dusky_root = Path.home() / "user_scripts" / "dusky_tui"
if str(_dusky_root) not in sys.path:
    sys.path.insert(0, str(_dusky_root))

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

TABS = ["Networks", "Saved", "Hotspot", "Status"]

SCHEMA = {0: [], 1: [], 2: [], 3: []}

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

SCHEMA[1].append(ConfigItem(
    label="  ⟳  Loading saved profiles...",
    key="loading_saved",
    scope="saved",
    type_="action",
    default=":",
    group="Saved Connections"
))

# ============================================================================
#  Tab 2: Hotspot — static layout, labels updated dynamically by engine
# ============================================================================
SCHEMA[2].extend([
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

# ============================================================================
#  Tab 3: Status — static layout, labels updated dynamically by engine
# ============================================================================
SCHEMA[3].extend([
    ConfigItem(
        label="Wi-Fi Radio Switch",
        key="wifi_radio",
        scope="status",
        type_="bool",
        default=True,
        group="Hardware",
        extended_help="Enable or disable the wireless radio hardware interface."
    ),
    ConfigItem(
        label="Connected WiFi: None",
        key="status_ssid",
        scope="clipboard",
        type_="bool",
        default=False,
        options=["copy"],
        group="Connection Info"
    ),
    ConfigItem(
        label="IP Address:     N/A",
        key="status_ip",
        scope="clipboard",
        type_="bool",
        default=False,
        options=["copy"],
        group="Connection Info"
    ),
    ConfigItem(
        label="Gateway:        N/A",
        key="status_gateway",
        scope="clipboard",
        type_="bool",
        default=False,
        options=["copy"],
        group="Connection Info"
    ),
    ConfigItem(
        label="DNS Server:     N/A",
        key="status_dns",
        scope="clipboard",
        type_="bool",
        default=False,
        options=["copy"],
        group="Connection Info"
    ),
    ConfigItem(
        label="WiFi Device:    N/A",
        key="status_device",
        scope="clipboard",
        type_="bool",
        default=False,
        options=["copy"],
        group="Connection Info"
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
        extended_help="Restarts the systemd NetworkManager daemon in case of hangs. Requires sudo authentication."
    ),
    ConfigItem(
        label="Force Wireless Interface Rescan",
        key="rescan",
        scope="status_action",
        type_="bool",
        default=False,
        options=["trigger"],
        group="Actions",
        extended_help="Forces NetworkManager to perform an immediate rescan of wireless networks."
    )
])

# =============================================================================
# DIRECT EXECUTION HANDLER
# =============================================================================
if __name__ == "__main__":
    import sys, subprocess
    from pathlib import Path

    script_path = Path(__file__).resolve()
    main_router = Path.home() / "user_scripts" / "dusky_tui" / "python" / "main" / "main.py"

    if main_router.exists():
        sys.exit(subprocess.run([sys.executable, str(main_router), str(script_path)] + sys.argv[1:]).returncode)
    else:
        print(f"[-] Error: Main Dusky TUI router not found at {main_router}", file=sys.stderr)
        sys.exit(1)
