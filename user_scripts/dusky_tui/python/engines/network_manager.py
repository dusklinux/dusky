import os
import sys
import time
import math
import json
import re
import shutil
import logging
import subprocess
import threading
from pathlib import Path
from typing import Any

from python.frontend.core_types import BaseEngine, ConfigItem

logger = logging.getLogger("dusky_network_engine")

# =============================================================================
#  NMCLI OUTPUT PARSER
# =============================================================================
_NMCLI_FIELD_SPLIT = re.compile(r'(?<!\\):')

def _split_nmcli_line(line: str) -> list[str]:
    """Split an nmcli -t output line by unescaped colons, then unescape fields."""
    return [f.replace("\\:", ":") for f in _NMCLI_FIELD_SPLIT.split(line)]

# =============================================================================
#  PURE MODEL FUNCTIONS (Ported directly from Model.js)
# =============================================================================

def parse_network_status(raw: str) -> dict[str, Any]:
    parts = (raw or "disconnected\t\t\t").rstrip("\r\n").split("\t")
    kind = parts[0] if len(parts) > 0 and parts[0] else "disconnected"
    label = parts[1] if len(parts) > 1 else ""
    try:
        signal_strength = int(parts[2]) if len(parts) > 2 and parts[2] != "" else -1
    except ValueError:
        signal_strength = -1
    frequency = parts[3] if len(parts) > 3 else ""
    return {
        "kind": kind,
        "label": label,
        "signal_strength": signal_strength,
        "frequency": frequency,
    }

def wifi_icon_for(strength: int) -> str:
    icons = ["󰤯", "󰤟", "󰤢", "󰤥", "󰤨"]
    idx = max(0, min(4, math.ceil(strength / 20) - 1))
    return icons[idx]

def connection_icon(kind: str, signal_strength: int) -> str:
    if kind == "wifi":
        return wifi_icon_for(signal_strength)
    if kind == "ethernet":
        return "󰈀"
    return "󰤮"

def format_header_speed(mbps: str | int | float) -> str:
    try:
        v = int(float(mbps))
    except (ValueError, TypeError):
        return ""
    if v <= 0:
        return ""
    if v >= 1000:
        val = v / 1000.0
        return f"{val:.0f}gbit" if v % 1000 == 0 else f"{val:.1f}gbit"
    return f"{v}mbit"

def format_header_freq(mhz: str | int | float) -> str:
    try:
        v = float(mhz)
    except (ValueError, TypeError):
        return ""
    if not v or v <= 0:
        return ""
    if 2400 <= v < 2500:
        return "2.4ghz"
    if 4900 <= v < 5925:
        return "5ghz"
    if 5925 <= v < 7125:
        return "6ghz"
    if 57000 <= v < 71000:
        return "60ghz"
    ghz = v / 1000.0
    return f"{ghz:.0f}ghz" if ghz % 1 == 0 else f"{ghz:.1f}ghz"

def header_detail(info: dict[str, Any]) -> str:
    val = info or {}
    t = val.get("type", "")
    if t == "ethernet":
        return format_header_speed(val.get("speed", ""))
    if t == "wifi":
        return format_header_freq(val.get("freq", ""))
    return ""

def parse_key_value(raw: str) -> dict[str, str]:
    next_dict: dict[str, str] = {}
    lines = (raw or "").splitlines()
    for line in lines:
        if not line:
            continue
        idx = line.find("\t")
        if idx == -1:
            continue
        key = line[:idx]
        val = line[idx + 1:].strip()
        next_dict[key] = val
    return next_dict

def throughput_state(previous: dict[str, Any] | None, next_sample: dict[str, Any] | None, now: float) -> dict[str, Any]:
    prev = previous or {}
    sample = next_sample or {}
    iface = sample.get("iface", "")
    try:
        rx = float(sample.get("rx_bytes", "0"))
    except ValueError:
        rx = 0.0
    try:
        tx = float(sample.get("tx_bytes", "0"))
    except ValueError:
        tx = 0.0

    prev_time = float(prev.get("prev_sample_time", 0))

    if iface != prev.get("prev_iface", "") or prev_time == 0:
        return {
            "prev_iface": iface,
            "prev_rx_bytes": rx,
            "prev_tx_bytes": tx,
            "prev_sample_time": now,
            "download_rate": 0.0,
            "upload_rate": 0.0,
            "total_rx": rx,
            "total_tx": tx,
        }

    dl_rate = float(prev.get("download_rate", 0.0))
    ul_rate = float(prev.get("upload_rate", 0.0))
    dt = now - prev_time

    if dt > 0:
        dl_rate = max(0.0, (rx - float(prev.get("prev_rx_bytes", 0))) / dt)
        ul_rate = max(0.0, (tx - float(prev.get("prev_tx_bytes", 0))) / dt)

    return {
        "prev_iface": iface,
        "prev_rx_bytes": rx,
        "prev_tx_bytes": tx,
        "prev_sample_time": now,
        "download_rate": dl_rate,
        "upload_rate": ul_rate,
        "total_rx": rx,
        "total_tx": tx,
    }

def ping_sample_value(raw: Any) -> float | None:
    try:
        v = float(raw)
        if math.isnan(v) or math.isinf(v) or v < 0:
            return None
        return v
    except (ValueError, TypeError):
        return None

def append_ping_sample(samples: list[float | None] | None, raw: Any, limit: int) -> list[float | None]:
    values = list(samples) if isinstance(samples, list) else []
    values.append(ping_sample_value(raw))
    while len(values) > limit:
        values.pop(0)
    return values

def average_ping_latency(samples: list[float | None] | None, limit: int) -> float:
    values = list(samples) if isinstance(samples, list) else []
    sample_limit = max(1, int(limit) if limit else len(values) or 1)
    total = 0.0
    count = 0
    start = max(0, len(values) - sample_limit)
    for i in range(start, len(values)):
        v = values[i]
        if v is not None and isinstance(v, (int, float)) and not math.isnan(v) and v >= 0:
            total += v
            count += 1
    return total / count if count > 0 else -1.0

def ping_packet_loss_percent(samples: list[float | None] | None) -> int:
    values = list(samples) if isinstance(samples, list) else []
    if not values:
        return 0
    lost = sum(1 for v in values if v is None)
    return round((lost / len(values)) * 100)

def format_packet_loss(percent: int | str | float) -> str:
    try:
        val = int(percent)
    except (ValueError, TypeError):
        val = 0
    return f"{max(0, val)}%"

def ping_latency_state(previous: dict[str, Any] | None, next_sample: dict[str, Any] | None, limit: int = 24, average_limit: int = 5) -> dict[str, Any]:
    prev = previous or {}
    sample = next_sample or {}
    iface = sample.get("iface", "")
    window = max(1, int(limit) if limit else 24)
    avg_window = max(1, int(average_limit) if average_limit else 5)

    reset = not iface or iface != prev.get("ping_iface", "")
    router_samples = [] if reset else prev.get("router_ping_samples", [])
    internet_samples = [] if reset else prev.get("internet_ping_samples", [])

    if "router_ping_ms" in sample:
        router_samples = append_ping_sample(router_samples, sample["router_ping_ms"], window)
    elif reset:
        router_samples = []

    if "internet_ping_ms" in sample:
        internet_samples = append_ping_sample(internet_samples, sample["internet_ping_ms"], window)
    elif reset:
        internet_samples = []

    return {
        "ping_iface": iface,
        "router_ping_samples": router_samples,
        "internet_ping_samples": internet_samples,
        "router_ping_latency": average_ping_latency(router_samples, avg_window),
        "internet_ping_latency": average_ping_latency(internet_samples, avg_window),
        "internet_ping_packet_loss": ping_packet_loss_percent(internet_samples),
    }

def format_bytes(bytes_val: float | int | str) -> str:
    try:
        n = float(bytes_val)
    except (ValueError, TypeError):
        n = 0.0
    if math.isnan(n) or n < 0:
        n = 0.0
    if n < 1024:
        return f"{round(n)} B"
    if n < 1024 * 1024:
        return f"{n / 1024:.1f} KB"
    if n < 1024 * 1024 * 1024:
        return f"{n / (1024 * 1024):.1f} MB"
    return f"{n / (1024 * 1024 * 1024):.2f} GB"

def format_rate(bytes_per_sec: float | int | str) -> str:
    return f"{format_bytes(bytes_per_sec)}/s"

def format_speed_mbps(mbps: float | int | str) -> str:
    try:
        v = float(mbps)
    except (ValueError, TypeError):
        return "--"
    if math.isnan(v) or math.isinf(v) or v <= 0:
        return "--"
    if 0 < v < 10:
        return f"{v:.1f} Mbps"
    return f"{round(v + 1e-9)} Mbps"

def format_ping_latency(ms: float | int | str | None) -> str:
    if ms is None:
        return "Timeout"
    try:
        v = float(ms)
    except (ValueError, TypeError):
        return "Timeout"
    if math.isnan(v) or v < 0:
        return "Timeout"
    return f"{v:.1f} ms" if 0 < v < 10 else f"{v:.1f} ms"

def wifi_row(network: dict[str, Any]) -> dict[str, Any] | None:
    if not network:
        return None
    return {
        "network": network,
        "connected": bool(network.get("connected") or network.get("in_use")),
        "known": bool(network.get("known", False)),
        "ssid": network.get("ssid") or network.get("name") or "",
        "signal": round(network.get("signal", network.get("signalStrength", 0))),
        "security": network.get("security", "Open"),
    }

def sort_wifi_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    nets = list(rows) if isinstance(rows, list) else []
    nets.sort(key=lambda x: (not x.get("connected", False), not x.get("known", False), -x.get("signal", 0)))
    return nets

def wifi_section_title(wifi_networks: list[dict[str, Any]], index: int) -> str:
    networks = list(wifi_networks) if isinstance(wifi_networks, list) else []
    if index < 0 or index >= len(networks):
        return ""
    net = networks[index]
    if not net:
        return ""
    if net.get("known") and index == 0:
        return "KNOWN NETWORKS"
    if not net.get("known") and (index == 0 or (index > 0 and networks[index - 1].get("known"))):
        return "OTHER NETWORKS"
    return ""

def is_protected(security: str, open_security: str = "Open") -> bool:
    return security != open_security and security not in ("--", "", "None")

def network_failure_reason(reason: str, reasons: dict[str, str] | None = None) -> str:
    r = reasons or {}
    if reason == r.get("NoSecrets"):
        return "Passphrase required"
    if reason == r.get("WifiAuthTimeout"):
        return "Wrong password"
    if reason == r.get("WifiNetworkLost"):
        return "Network lost"
    if reason == r.get("WifiClientDisconnected"):
        return "Disconnected"
    if reason == r.get("WifiClientFailed"):
        return "Connection failed"
    return "Failed to connect"

# CamelCase aliases for pure JavaScript Model.js functions
parseNetworkStatus = parse_network_status
wifiIconFor = wifi_icon_for
connectionIcon = connection_icon
formatHeaderSpeed = format_header_speed
formatHeaderFreq = format_header_freq
headerDetail = header_detail
parseKeyValue = parse_key_value
throughputState = throughput_state
pingLatencyState = ping_latency_state
appendPingSample = append_ping_sample
averagePingLatency = average_ping_latency
pingPacketLossPercent = ping_packet_loss_percent
formatPacketLoss = format_packet_loss
formatBytes = format_bytes
formatRate = format_rate
formatSpeedMbps = format_speed_mbps
formatPingLatency = format_ping_latency
wifiRow = wifi_row
sortWifiRows = sort_wifi_rows
wifiSectionTitle = wifi_section_title
isProtected = is_protected
networkFailureReason = network_failure_reason


# =============================================================================
#  ENGINE IMPLEMENTATION
# =============================================================================
class NetworkManagerEngine(BaseEngine):
    """
    Full NetworkManager & Dusky Network logic engine for Dusky TUI.
    """
    _instance: "NetworkManagerEngine | None" = None

    def __init__(self, config_path: str = ""):
        NetworkManagerEngine._instance = self
        self.cache_dir = Path.home() / ".cache" / "dusky_tui"
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self._target_path = str(self.cache_dir / "wifi_cache.json")

        self.app = None
        self.shutdown_event = threading.Event()
        self.rescan_event = threading.Event()

        # In-memory hotspot config
        self._hotspot_ssid = "MyHotspot"
        self._hotspot_password = ""

        # Live state tracking
        self._tp_state: dict[str, Any] = {}
        self._ping_state: dict[str, Any] = {}
        self._verbose_info: dict[str, str] = {}
        self._dns_provider: str = "DHCP"
        self._dns_servers_str: str = ""

        # Speed test state
        self._speedtest_running: bool = False
        self._speedtest_status: str = "Ready"
        self._speedtest_down_val: str = "--"
        self._speedtest_up_val: str = "--"

        # Load cached scan results for instant startup
        self._cached_scans: list[dict[str, Any]] = []
        cache_path = Path(self._target_path)
        if cache_path.exists():
            try:
                with open(cache_path, "r", encoding="utf-8") as f:
                    self._cached_scans = json.load(f)
            except Exception as e:
                logger.error(f"Error loading wifi cache: {e}")

        # Start background loop
        self._bg_thread = threading.Thread(target=self._background_loop, daemon=True)
        self._bg_thread.start()

    def set_app(self, app) -> None:
        self.app = app
        self.rescan_event.set()

    @property
    def target_path(self) -> str:
        return self._target_path

    # =========================================================================
    #  BaseEngine Contract
    # =========================================================================

    def load_state(self) -> dict[str, Any]:
        state: dict[str, Any] = {}

        radio = self._run_cmd(["nmcli", "radio", "wifi"]).strip()
        state["status/wifi_radio"] = "true" if radio == "enabled" else "false"

        for conn in self._get_saved_wifi():
            state[f"saved/{conn['uuid']}"] = "true" if conn["autoconnect"] else "false"

        # Hotspot config
        state["hotspot/hotspot_ssid"] = self._hotspot_ssid
        state["hotspot/hotspot_password"] = self._hotspot_password

        active = self._get_active_wifi_connection()
        state["hotspot/hotspot_status_info"] = "Active" if active and active.get("mode") == "ap" else "Inactive"

        # Trigger bools
        state["network/rescan"] = "false"
        state["hotspot/start_hotspot_24"] = "false"
        state["hotspot/start_hotspot_5"] = "false"
        state["hotspot/stop_hotspot"] = "false"
        state["status_action/disconnect"] = "false"
        state["status_action/restart_nm"] = "false"
        state["status_action/rescan"] = "false"

        # DNS actions
        state["dns_action/dns_dhcp"] = "false"
        state["dns_action/dns_cloudflare"] = "false"
        state["dns_action/dns_google"] = "false"
        state["dns_action/dns_custom"] = self._dns_servers_str

        # Speed test actions
        state["speedtest_action/speedtest_full"] = "false"
        state["speedtest_action/speedtest_down"] = "false"
        state["speedtest_action/speedtest_up"] = "false"

        # Clipboard copy items
        for clip_key in (
            "status_type", "status_ssid", "status_ip", "status_gateway", "status_detail", "status_device",
            "throughput_down", "throughput_up", "throughput_rx_total", "throughput_tx_total",
            "ping_router", "ping_internet", "ping_packet_loss",
            "speedtest_down_result", "speedtest_up_result"
        ):
            state[f"clipboard/{clip_key}"] = "false"

        if self.app and hasattr(self.app, 'schema'):
            for tab_idx in range(len(self.app.schema)):
                for item in self.app.schema.get(tab_idx, []):
                    if item.type_ in ("action", "menu"):
                        continue
                    cache_key = f"{item.scope}/{item.key}" if item.scope else item.key
                    if cache_key not in state:
                        state[cache_key] = item.serialize(item.value)

        return state

    def write_value(self, target_key: str, target_scope: str, new_value: str, item_type: str = "string") -> tuple[bool, str, str]:
        logger.info(f"write_value: key={target_key}, scope={target_scope}, val={new_value}")

        # ---- Rescan button ----
        if target_key == "rescan":
            self.rescan_event.set()
            return True, "WiFi rescan triggered.", ""

        # ---- Radio toggle ----
        if target_key == "wifi_radio":
            action = "on" if new_value == "true" else "off"
            res = subprocess.run(
                ["nmcli", "radio", "wifi", action],
                capture_output=True, text=True, stdin=subprocess.DEVNULL, timeout=10
            )
            if res.returncode == 0:
                self.rescan_event.set()
                return True, f"WiFi radio turned {action}.", ""
            return False, f"Failed to set radio: {res.stderr.strip()}", res.stderr

        # ---- Autoconnect toggle ----
        if target_scope == "saved" and self._is_uuid(target_key):
            yn = "yes" if new_value == "true" else "no"
            res = subprocess.run(
                ["nmcli", "connection", "modify", "uuid", target_key, "connection.autoconnect", yn],
                capture_output=True, text=True, stdin=subprocess.DEVNULL, timeout=10
            )
            if res.returncode == 0:
                return True, f"Autoconnect set to {yn}.", ""
            return False, f"Failed: {res.stderr.strip()}", res.stderr

        # ---- Hotspot configuration ----
        if target_scope == "hotspot":
            return self._handle_hotspot(target_key, new_value)

        # ---- Network actions ----
        if target_scope == "network":
            return self._handle_network_action(target_key, new_value)

        # ---- Saved profile actions ----
        if target_scope == "saved_action":
            return self._handle_saved_action(target_key)

        # ---- Status tab actions ----
        if target_scope == "status_action":
            return self._handle_status_action(target_key)

        # ---- DNS tab actions ----
        if target_scope == "dns_action":
            return self._handle_dns_action(target_key, new_value)

        # ---- Speed Test tab actions ----
        if target_scope == "speedtest_action":
            return self._handle_speedtest_action(target_key)

        # ---- Clipboard copy ----
        if target_scope == "clipboard":
            return self._handle_clipboard(target_key)

        return True, "OK", ""

    # =========================================================================
    #  ACTION HANDLERS
    # =========================================================================

    def _handle_hotspot(self, key: str, value: str) -> tuple[bool, str, str]:
        if key == "hotspot_ssid":
            self._hotspot_ssid = value
            return True, "Hotspot SSID updated.", ""

        if key == "hotspot_password":
            if value and len(value) < 8:
                return False, "Password must be at least 8 characters.", ""
            self._hotspot_password = value
            return True, "Hotspot password updated.", ""

        if key in ("start_hotspot_24", "start_hotspot_5"):
            band = "bg" if key == "start_hotspot_24" else "a"
            wifi_dev = self._get_wifi_device()
            if not wifi_dev:
                return False, "No WiFi device found.", ""

            cmd = ["nmcli", "device", "wifi", "hotspot", "ifname", wifi_dev,
                   "ssid", self._hotspot_ssid, "band", band]
            if self._hotspot_password:
                cmd.extend(["password", self._hotspot_password])

            res = subprocess.run(cmd, capture_output=True, text=True, stdin=subprocess.DEVNULL, timeout=15)
            if res.returncode == 0:
                self.rescan_event.set()
                return True, "Hotspot started!", res.stdout
            return False, f"Failed: {res.stderr.strip()}", res.stderr

        if key == "stop_hotspot":
            wifi_dev = self._get_wifi_device()
            if not wifi_dev:
                return False, "No WiFi device.", ""
            res = subprocess.run(
                ["nmcli", "device", "disconnect", wifi_dev],
                capture_output=True, text=True, stdin=subprocess.DEVNULL, timeout=10
            )
            if res.returncode == 0:
                self.rescan_event.set()
                return True, "Hotspot stopped.", ""
            return False, f"Failed: {res.stderr.strip()}", res.stderr

        return True, "OK", ""

    def _handle_network_action(self, key: str, value: str) -> tuple[bool, str, str]:
        if key.startswith("pw__"):
            ssid = key[4:]
            if not value:
                return False, "Password cannot be empty.", ""
            threading.Thread(target=self._async_connect, args=(ssid, value), daemon=True).start()
            return True, f"Connecting to {ssid}...", ""

        if key.startswith("cn__"):
            ssid = key[4:]
            saved = self._get_saved_wifi()
            match = [c for c in saved if c["name"] == ssid]
            if match:
                threading.Thread(target=self._async_connect_saved, args=(ssid, match[0]["uuid"]), daemon=True).start()
            else:
                threading.Thread(target=self._async_connect, args=(ssid, None), daemon=True).start()
            return True, f"Connecting to {ssid}...", ""

        if key.startswith("dc__"):
            ssid = key[4:]
            active = self._get_active_wifi_connection()
            if active and active["ssid"] == ssid:
                threading.Thread(target=self._async_disconnect, args=(ssid, active["uuid"]), daemon=True).start()
                return True, f"Disconnecting from {ssid}...", ""
            return False, "Not connected to this network.", ""

        if key.startswith("fg__"):
            ssid = key[4:]
            saved = self._get_saved_wifi()
            match = [c for c in saved if c["name"] == ssid]
            if match:
                res = subprocess.run(
                    ["nmcli", "connection", "delete", "uuid", match[0]["uuid"]],
                    capture_output=True, text=True, stdin=subprocess.DEVNULL, timeout=10
                )
                if res.returncode == 0:
                    self.rescan_event.set()
                    return True, f"Forgot {ssid}.", ""
                return False, f"Failed: {res.stderr.strip()}", res.stderr
            return False, "Connection not found.", ""

        return True, "OK", ""

    def _handle_saved_action(self, key: str) -> tuple[bool, str, str]:
        if key.startswith("cn__"):
            uuid = key[4:]
            threading.Thread(target=self._async_connect_saved, args=(uuid, uuid), daemon=True).start()
            return True, "Connecting...", ""

        if key.startswith("dc__"):
            uuid = key[4:]
            threading.Thread(target=self._async_disconnect, args=(uuid, uuid), daemon=True).start()
            return True, "Disconnecting...", ""

        if key.startswith("fg__"):
            uuid = key[4:]
            res = subprocess.run(
                ["nmcli", "connection", "delete", "uuid", uuid],
                capture_output=True, text=True, stdin=subprocess.DEVNULL, timeout=10
            )
            if res.returncode == 0:
                self.rescan_event.set()
                return True, "Deleted.", ""
            return False, f"Failed: {res.stderr.strip()}", res.stderr

        return True, "OK", ""

    def _handle_status_action(self, key: str) -> tuple[bool, str, str]:
        if key == "disconnect":
            active = self._get_active_wifi_connection()
            if active:
                threading.Thread(target=self._async_disconnect, args=(active["ssid"], active["uuid"]), daemon=True).start()
                return True, "Disconnecting...", ""
            return False, "No active connection.", ""

        if key == "restart_nm":
            res = subprocess.run(
                ["sudo", "-n", "systemctl", "restart", "NetworkManager"],
                capture_output=True, text=True, stdin=subprocess.DEVNULL, timeout=15
            )
            if res.returncode == 0:
                self.rescan_event.set()
                return True, "NetworkManager restarted.", ""
            err = res.stderr.strip().lower()
            if "password is required" in err or "sudo:" in err:
                return False, "AUTH_REQUIRED", res.stderr
            return False, f"Failed: {res.stderr.strip()}", res.stderr

        if key == "rescan":
            self.rescan_event.set()
            return True, "Rescan triggered.", ""

        return True, "OK", ""

    def _handle_dns_action(self, key: str, value: str) -> tuple[bool, str, str]:
        dns_script = self._find_script("omarchy-dns")
        env = self._get_exec_env()

        if key == "dns_dhcp":
            cmd = [dns_script, "DHCP"]
        elif key == "dns_cloudflare":
            cmd = [dns_script, "Cloudflare"]
        elif key == "dns_google":
            cmd = [dns_script, "Google"]
        elif key == "dns_custom":
            if not value:
                return False, "Custom DNS servers string cannot be empty.", ""
            self._dns_servers_str = value
            cmd = [dns_script, "Custom"]
        else:
            return True, "OK", ""

        try:
            res = subprocess.run(
                cmd,
                input=(value + "\n") if key == "dns_custom" else None,
                capture_output=True, text=True, env=env, timeout=20
            )
            if res.returncode == 0:
                self.rescan_event.set()
                return True, f"DNS updated to {key.replace('dns_', '').upper()}.", res.stdout
            else:
                err = res.stderr.strip()
                if "sudo" in err.lower() or "root" in err.lower() or "password" in err.lower():
                    return False, "AUTH_REQUIRED", res.stderr
                return False, f"Failed: {err}", res.stderr
        except Exception as e:
            return False, f"Error setting DNS: {e}", str(e)

    def _handle_speedtest_action(self, key: str) -> tuple[bool, str, str]:
        if self._speedtest_running:
            return False, "Speed test is already running.", ""

        mode = "full"
        if key == "speedtest_down":
            mode = "down"
        elif key == "speedtest_up":
            mode = "up"

        if self.app:
            def run_interactive_speedtest():
                self._speedtest_running = True
                self._speedtest_status = f"Running interactive {mode} test..."
                if hasattr(self.app, "_option_cache"):
                    self.app._option_cache.clear()
                self.app._rebuild_indexes()
                self.app._refresh_all_ui()

                try:
                    rich_script = str(Path(__file__).parent / "rich_speedtest.py")
                    with self.app.suspend():
                        subprocess.run([sys.executable, rich_script, mode])

                    # Read results after interactive run
                    res_file = Path.home() / ".cache" / "dusky_tui" / "speedtest_last.json"
                    if res_file.exists():
                        try:
                            with open(res_file, "r") as f:
                                data = json.load(f)
                                if data.get("down") is not None:
                                    self._speedtest_down_val = f"{data['down']:.1f} Mbps"
                                if data.get("up") is not None:
                                    self._speedtest_up_val = f"{data['up']:.1f} Mbps"
                                self._speedtest_status = "Complete"
                        except Exception:
                            pass
                finally:
                    self._speedtest_running = False
                    self._rebuild_schema()

            self.app.call_from_thread(run_interactive_speedtest)
            return True, f"Started interactive {mode} speed test...", ""

        threading.Thread(target=self._async_run_speedtest, args=(mode,), daemon=True).start()
        return True, f"Started {mode} speed test...", ""

    def _handle_clipboard(self, key: str) -> tuple[bool, str, str]:
        if not self.app:
            return False, "App not ready.", ""

        target_item = None
        for tab_idx in range(len(self.app.schema)):
            for item in self.app.schema.get(tab_idx, []):
                if item.key == key and item.scope == "clipboard":
                    target_item = item
                    break
            if target_item:
                break

        if not target_item:
            return False, "Item not found.", ""

        label = target_item.label
        if ":" in label:
            val = label.split(":", 1)[1].strip()
        else:
            val = label.strip()

        if not val or val in ("N/A", "None", "--"):
            return False, "Nothing to copy.", ""

        try:
            subprocess.run(["wl-copy", val], stdin=subprocess.DEVNULL, capture_output=True, timeout=3)
            return True, f"Copied: {val}", ""
        except FileNotFoundError:
            try:
                subprocess.run(["xclip", "-selection", "clipboard"], input=val.encode(), capture_output=True, timeout=3)
                return True, f"Copied: {val}", ""
            except FileNotFoundError:
                return False, "No clipboard tool found (wl-copy/xclip).", ""

    def _safe_call_from_thread(self, func, *args) -> None:
        if not self.app:
            return
        try:
            call_fn = getattr(self.app, "call_from_thread", None)
            if call_fn and callable(call_fn):
                call_fn(func, *args)
        except Exception as e:
            logger.debug(f"call_from_thread error: {e}")

    def _async_rescan_wifi(self) -> None:
        try:
            if hasattr(self.app, "notify_status"):
                self._safe_call_from_thread(self.app.notify_status, "Scanning WiFi networks...")
            subprocess.run(
                ["nmcli", "device", "wifi", "list", "--rescan", "yes"],
                capture_output=True, stdin=subprocess.DEVNULL, timeout=15
            )
            self._cached_scans = self._get_scanned_wifi()
            try:
                with open(self._target_path, "w", encoding="utf-8") as f:
                    json.dump(self._cached_scans, f)
            except Exception as e:
                logger.error(f"Cache write error: {e}")
            self._safe_call_from_thread(self._rebuild_schema)
        except Exception as e:
            logger.error(f"Async Wi-Fi scan error: {e}")

    def _enrich_network_status(self, verb: dict[str, str], active_wifi: dict[str, Any] | None) -> dict[str, Any]:
        enriched = dict(verb)

        # 1. Physical connection type & SSID resolution
        if active_wifi:
            enriched["type"] = "wifi"
            enriched["ssid"] = active_wifi.get("ssid", "")
            if not enriched.get("iface") or not Path(f"/sys/class/net/{enriched['iface']}/wireless").exists():
                enriched["phy_iface"] = active_wifi.get("device", "wlan0")
            else:
                enriched["phy_iface"] = enriched["iface"]
        elif Path(f"/sys/class/net/{enriched.get('iface', '')}/wireless").exists():
            enriched["type"] = "wifi"
            enriched["phy_iface"] = enriched["iface"]
        elif enriched.get("iface") and not enriched.get("type"):
            enriched["type"] = "ethernet"

        phy_iface = enriched.get("phy_iface", enriched.get("iface", ""))

        # 2. Wi-Fi details fallback if missing
        if enriched.get("type") == "wifi" and phy_iface:
            if not enriched.get("freq") or not enriched.get("ssid"):
                try:
                    iw_out = self._run_cmd(["iw", "dev", phy_iface, "link"])
                    if iw_out:
                        for line in iw_out.splitlines():
                            line_str = line.strip()
                            if line_str.startswith("SSID:"):
                                enriched["ssid"] = line_str.split("SSID:", 1)[1].strip()
                            elif line_str.startswith("freq:"):
                                enriched["freq"] = line_str.split("freq:", 1)[1].strip()
                            elif "tx bitrate:" in line_str:
                                parts = line_str.split("tx bitrate:", 1)[1].strip().split()
                                if len(parts) >= 2:
                                    enriched["bitrate"] = f"{parts[0]} {parts[1]}"
                except Exception:
                    pass

        # 3. Default Gateway fallback if missing or empty
        if not enriched.get("gateway"):
            try:
                route_out = self._run_cmd(["ip", "route", "show", "default"])
                match = re.search(r"default via ([\d.]+)", route_out)
                if match:
                    enriched["gateway"] = match.group(1)
            except Exception:
                pass

        # 4. Router ping fallback if missing
        gw = enriched.get("gateway")
        if gw and "router_ping_ms" not in enriched:
            try:
                ping_out = self._run_cmd(["ping", "-n", "-c", "1", "-W", "1", gw])
                m = re.search(r"time[=<]([\d.]+)", ping_out)
                if m:
                    enriched["router_ping_ms"] = m.group(1)
            except Exception:
                pass

        return enriched

    # =========================================================================
    #  BACKGROUND POLLING WORKER
    # =========================================================================

    def _background_loop(self) -> None:
        """Daemon thread: polls radio, active state, live throughput, ping stats every 1.5s."""
        last_radio: str | None = None
        last_active_uuid: str | None = None
        last_scan_time: float = time.time()

        while not self.shutdown_event.is_set():
            try:
                now = time.time()
                radio = self._run_cmd(["nmcli", "radio", "wifi"]).strip()
                active = self._get_active_wifi_connection()
                active_uuid = active["uuid"] if active else None

                # Poll verbose status using omarchy-network-status --verbose
                status_script = self._find_script("omarchy-network-status")
                raw_verbose = self._run_cmd([status_script, "--verbose"], timeout=5)
                verbose_info = parse_key_value(raw_verbose)

                # Enrich status with physical interface & real gateway detection
                enriched_info = self._enrich_network_status(verbose_info, active)
                self._verbose_info = enriched_info

                # Update live throughput state
                self._tp_state = throughput_state(self._tp_state, enriched_info, now)

                # Update live ping latency state
                self._ping_state = ping_latency_state(self._ping_state, enriched_info, limit=24, average_limit=5)

                # Poll DNS provider
                dns_script = self._find_script("omarchy-dns")
                self._dns_provider = self._run_cmd([dns_script], timeout=3).strip() or "DHCP"

                should_scan = self.rescan_event.is_set() or (now - last_scan_time > 25.0)

                if should_scan and radio == "enabled":
                    self.rescan_event.clear()
                    last_scan_time = now
                    threading.Thread(target=self._async_rescan_wifi, daemon=True).start()

                last_radio = radio
                last_active_uuid = active_uuid

                # Always refresh UI labels for live traffic / pings every iteration
                self._safe_call_from_thread(self._rebuild_schema)

            except Exception as e:
                logger.error(f"Background loop error: {e}")

            time.sleep(1.5)

    # =========================================================================
    #  SPEED TEST ASYNC WORKER
    # =========================================================================

    def _async_run_speedtest(self, mode: str) -> None:
        self._speedtest_running = True
        env = self._get_exec_env()
        speedtest_script = self._find_script("omarchy-network-speedtest")

        if mode in ("full", "down"):
            self._speedtest_status = "Testing Download..."
            self._speedtest_down_val = "Testing..."
            if self.app:
                self.app.call_from_thread(self._rebuild_schema)

            try:
                proc = subprocess.Popen(
                    [speedtest_script, "down"],
                    stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                    text=True, env=env, bufsize=1
                )
                last_val = "0.0"
                if proc.stdout:
                    for line in proc.stdout:
                        val = line.strip()
                        if val:
                            last_val = val
                            self._speedtest_down_val = format_speed_mbps(val)
                            if self.app:
                                self.app.call_from_thread(self._rebuild_schema)
                proc.wait()
                self._speedtest_down_val = format_speed_mbps(last_val)
            except Exception as e:
                self._speedtest_down_val = "Failed"
                logger.error(f"Download speed test error: {e}")

        if mode in ("full", "up"):
            self._speedtest_status = "Testing Upload..."
            self._speedtest_up_val = "Testing..."
            if self.app:
                self.app.call_from_thread(self._rebuild_schema)

            try:
                proc = subprocess.Popen(
                    [speedtest_script, "up"],
                    stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                    text=True, env=env, bufsize=1
                )
                last_val = "0.0"
                if proc.stdout:
                    for line in proc.stdout:
                        val = line.strip()
                        if val:
                            last_val = val
                            self._speedtest_up_val = format_speed_mbps(val)
                            if self.app:
                                self.app.call_from_thread(self._rebuild_schema)
                proc.wait()
                self._speedtest_up_val = format_speed_mbps(last_val)
            except Exception as e:
                self._speedtest_up_val = "Failed"
                logger.error(f"Upload speed test error: {e}")

        self._speedtest_status = "Test Completed"
        self._speedtest_running = False
        if self.app:
            self.app.call_from_thread(self._rebuild_schema)
            self.app.call_from_thread(self.app.notify_status, "Speed test completed!")

    # =========================================================================
    #  ASYNC CONNECTION HELPERS
    # =========================================================================

    def _async_connect(self, ssid: str, password: str | None) -> None:
        cmd = ["nmcli", "device", "wifi", "connect", ssid]
        if password:
            cmd.extend(["password", password])
        res = subprocess.run(cmd, capture_output=True, text=True, stdin=subprocess.DEVNULL, timeout=30)
        if self.app:
            if res.returncode == 0:
                self.app.call_from_thread(self.app.notify_status, f"Connected to {ssid}!")
            else:
                err = res.stderr.strip().split("\n")[0][:50]
                self.app.call_from_thread(self.app.notify_status, f"Failed: {err}")
                self.app.call_from_thread(self.app.play_reset_sound)
        self.rescan_event.set()

    def _async_connect_saved(self, label: str, uuid: str) -> None:
        res = subprocess.run(
            ["nmcli", "connection", "up", "uuid", uuid],
            capture_output=True, text=True, stdin=subprocess.DEVNULL, timeout=30
        )
        if self.app:
            if res.returncode == 0:
                self.app.call_from_thread(self.app.notify_status, f"Connected to {label}!")
            else:
                err = res.stderr.strip().split("\n")[0][:50]
                self.app.call_from_thread(self.app.notify_status, f"Failed: {err}")
                self.app.call_from_thread(self.app.play_reset_sound)
        self.rescan_event.set()

    def _async_disconnect(self, label: str, uuid: str) -> None:
        res = subprocess.run(
            ["nmcli", "connection", "down", "uuid", uuid],
            capture_output=True, text=True, stdin=subprocess.DEVNULL, timeout=15
        )
        if self.app:
            if res.returncode == 0:
                self.app.call_from_thread(self.app.notify_status, f"Disconnected from {label}.")
            else:
                err = res.stderr.strip().split("\n")[0][:50]
                self.app.call_from_thread(self.app.notify_status, f"Failed: {err}")
                self.app.call_from_thread(self.app.play_reset_sound)
        self.rescan_event.set()

    # =========================================================================
    #  DYNAMIC SCHEMA REBUILDER
    # =========================================================================

    def _rebuild_schema(self) -> None:
        """Rebuilds tabs 0 & 1 in-place. Updates live traffic, ping, DNS & speed test labels."""
        if not self.app or not self.app.schema:
            return

        radio = self._run_cmd(["nmcli", "radio", "wifi"]).strip() == "enabled"
        active = self._get_active_wifi_connection()
        saved = self._get_saved_wifi()

        expanded = set()
        for tab_idx in (0, 1):
            for item in self.app.schema.get(tab_idx, []):
                if item.is_parent and item.expanded:
                    uid = f"{item.scope}.{item.key}" if item.scope and item.scope != "DEFAULT" else item.key
                    expanded.add(uid)

        # ----- Tab 0: Networks -----
        t0 = []
        t0.append(self._make_item(
            label="⟳ Rescan Networks", key="rescan", scope="network",
            type_="bool", default=False, group="Actions",
            extended_help="Triggers a new wireless network scan in the background.",
            options=["trigger"]
        ))

        if not radio:
            t0.append(self._make_item(
                label="󰤮 Wi-Fi Radio is OFF — Enable in Status tab",
                key="radio_off_notice", scope="network", type_="action", default=":",
                group="Status"
            ))
        else:
            # Sort scanned wifi using pure sort_wifi_rows logic
            wifi_rows_data = []
            for net in self._cached_scans:
                match = [c for c in saved if c["name"] == net["ssid"]]
                row = wifi_row({
                    "ssid": net["ssid"],
                    "connected": net.get("in_use", False),
                    "known": len(match) > 0,
                    "signal": net.get("signal", 0),
                    "security": net.get("security", "Open"),
                })
                if row:
                    row["raw_net"] = net
                    row["match"] = match
                    wifi_rows_data.append(row)

            sorted_rows = sort_wifi_rows(wifi_rows_data)

            for idx, r in enumerate(sorted_rows):
                ssid = r["ssid"]
                signal = r["signal"]
                security = r["security"]
                in_use = r["connected"]
                is_saved = r["known"]
                match = r["match"]
                bar = self._signal_bar(signal)

                if in_use:
                    icon, status_lbl = "●", "Active"
                elif is_saved:
                    icon, status_lbl = "◉", "Saved"
                else:
                    icon, status_lbl = "○", "New"

                sec_title = wifi_section_title(sorted_rows, idx)
                group_name = sec_title if sec_title else "Available Networks"

                label = f"{icon} {status_lbl:<6} {ssid:<24} {security:<10} {signal}% {bar}"
                pkey = f"net__{ssid}"
                parent_uid = f"network.{pkey}"

                t0.append(self._make_item(
                    label=label, key=pkey, scope="network", type_="menu", default=None,
                    is_parent=True, expanded=(parent_uid in expanded), group=group_name
                ))

                if in_use:
                    t0.append(self._make_item(
                        label="✕ Disconnect", key=f"dc__{ssid}", scope="network",
                        type_="bool", default=False, parent_ref=parent_uid, options=["trigger"]
                    ))
                    t0.append(self._make_item(
                        label="✕ Forget", key=f"fg__{ssid}", scope="network",
                        type_="bool", default=False, parent_ref=parent_uid, options=["trigger"],
                        confirm_message=f"Permanently delete saved profile for **{ssid}**?"
                    ))
                elif is_saved:
                    uuid = match[0]["uuid"]
                    t0.append(self._make_item(
                        label="▶ Connect", key=f"cn__{ssid}", scope="network",
                        type_="bool", default=False, parent_ref=parent_uid, options=["trigger"]
                    ))
                    t0.append(self._make_item(
                        label="✕ Forget", key=f"fg__{ssid}", scope="network",
                        type_="bool", default=False, parent_ref=parent_uid, options=["trigger"],
                        confirm_message=f"Permanently delete saved profile for **{ssid}**?"
                    ))
                    t0.append(self._make_item(
                        label="Auto-connect", key=uuid, scope="saved",
                        type_="bool", default=match[0]["autoconnect"], parent_ref=parent_uid
                    ))
                else:
                    if is_protected(security):
                        t0.append(self._make_item(
                            label="▶ Connect (Enter Password)", key=f"pw__{ssid}",
                            scope="network", type_="string", default="", parent_ref=parent_uid
                        ))
                    else:
                        t0.append(self._make_item(
                            label="▶ Connect (Open)", key=f"cn__{ssid}", scope="network",
                            type_="bool", default=False, parent_ref=parent_uid, options=["trigger"]
                        ))

        self.app.schema[0] = t0

        # ----- Tab 1: Saved Profiles -----
        t1 = []
        for conn in saved:
            name, uuid, autocon = conn["name"], conn["uuid"], conn["autoconnect"]
            is_active = active and active["uuid"] == uuid
            indicator = "●" if is_active else "◉"
            pkey = f"prof__{uuid}"
            parent_uid = f"saved.{pkey}"

            t1.append(self._make_item(
                label=f"{indicator} {name}", key=pkey, scope="saved", type_="menu",
                default=None, is_parent=True, expanded=(parent_uid in expanded),
                group="Saved Connections"
            ))

            if is_active:
                t1.append(self._make_item(
                    label="✕ Disconnect", key=f"dc__{uuid}", scope="saved_action",
                    type_="bool", default=False, parent_ref=parent_uid, options=["trigger"]
                ))
            else:
                t1.append(self._make_item(
                    label="▶ Connect", key=f"cn__{uuid}", scope="saved_action",
                    type_="bool", default=False, parent_ref=parent_uid, options=["trigger"]
                ))

            t1.append(self._make_item(
                label="Auto-connect", key=uuid, scope="saved",
                type_="bool", default=autocon, parent_ref=parent_uid
            ))
            t1.append(self._make_item(
                label="✕ Forget", key=f"fg__{uuid}", scope="saved_action",
                type_="bool", default=False, parent_ref=parent_uid, options=["trigger"],
                confirm_message=f"Permanently delete **{name}**?"
            ))

        self.app.schema[1] = t1

        # ----- Tab 2: Status & Live Traffic (update labels) -----
        verb = self._verbose_info
        iface_name = verb.get("iface", "")
        conn_type = verb.get("type", "disconnected" if not iface_name else "ethernet")
        ssid_label = verb.get("ssid", active["ssid"] if active else "None")
        ip_label = verb.get("ip", "N/A")
        prefix_label = verb.get("prefix", "")
        if ip_label != "N/A" and prefix_label:
            ip_label = f"{ip_label}/{prefix_label}"
        gateway_label = verb.get("gateway", "N/A") or "N/A"

        # Detail string using header_detail pure logic
        link_detail_str = header_detail(verb) or verb.get("bitrate", "N/A")

        # Connection status string using connection_icon pure logic
        sig_dbm = verb.get("signal_dbm", "")
        sig_pct = 70 if sig_dbm else 0
        icon_str = connection_icon(conn_type, sig_pct)
        conn_status_label = f"{icon_str} {conn_type.upper()} ({ssid_label})"

        # Throughput & Ping values
        dl_rate_str = format_rate(self._tp_state.get("download_rate", 0))
        ul_rate_str = format_rate(self._tp_state.get("upload_rate", 0))
        rx_total_str = format_bytes(self._tp_state.get("total_rx", verb.get("rx_bytes", 0)))
        tx_total_str = format_bytes(self._tp_state.get("total_tx", verb.get("tx_bytes", 0)))

        router_ping_str = format_ping_latency(self._ping_state.get("router_ping_latency"))
        internet_ping_str = format_ping_latency(self._ping_state.get("internet_ping_latency"))
        packet_loss_str = format_packet_loss(self._ping_state.get("internet_ping_packet_loss", 0))

        if len(self.app.schema) > 2:
            for item in self.app.schema[2]:
                if item.key == "wifi_radio":
                    item.value = radio
                elif item.key == "status_type":
                    item.label = f"Connection:   {conn_status_label}"
                elif item.key == "status_ssid":
                    item.label = f"SSID / Name:  {ssid_label}"
                elif item.key == "status_ip":
                    item.label = f"IP Address:   {ip_label}"
                elif item.key == "status_gateway":
                    item.label = f"Gateway:      {gateway_label}"
                elif item.key == "status_detail":
                    item.label = f"Link Detail:  {link_detail_str}"
                elif item.key == "status_device":
                    item.label = f"Interface:    {iface_name or 'N/A'}"
                elif item.key == "throughput_down":
                    item.label = f"Download Rate: ↓ {dl_rate_str}"
                elif item.key == "throughput_up":
                    item.label = f"Upload Rate:   ↑ {ul_rate_str}"
                elif item.key == "throughput_rx_total":
                    item.label = f"Total Received:{rx_total_str}"
                elif item.key == "throughput_tx_total":
                    item.label = f"Total Sent:    {tx_total_str}"
                elif item.key == "ping_router":
                    item.label = f"Router Gateway Ping: {router_ping_str}"
                elif item.key == "ping_internet":
                    item.label = f"Internet Ping (1.1.1.1): {internet_ping_str}"
                elif item.key == "ping_packet_loss":
                    item.label = f"Packet Loss:         {packet_loss_str}"

        # ----- Tab 3: DNS (update labels) -----
        if len(self.app.schema) > 3:
            for item in self.app.schema[3]:
                if item.key == "dns_current":
                    item.label = f"Current DNS Provider: {self._dns_provider}"

        # ----- Tab 4: Speed Test (update labels) -----
        if len(self.app.schema) > 4:
            for item in self.app.schema[4]:
                if item.key == "speedtest_status":
                    item.label = f"Status: {self._speedtest_status}"
                elif item.key == "speedtest_down_result":
                    item.label = f"Download Speed: {self._speedtest_down_val}"
                elif item.key == "speedtest_up_result":
                    item.label = f"Upload Speed:   {self._speedtest_up_val}"

        # ----- Tab 5: Hotspot (update labels) -----
        if len(self.app.schema) > 5:
            if active and active.get("mode") == "ap":
                status_text = "Active"
                clients = self._get_hotspot_clients(active.get("device"))
                clients_text = f"{clients} connected"
            else:
                status_text = "Inactive"
                clients_text = "N/A"

            for item in self.app.schema[5]:
                if item.key == "hotspot_status_info":
                    item.label = f"Status: {status_text}"
                elif item.key == "hotspot_clients_info":
                    item.label = f"Connected Clients: {clients_text}"
                elif item.key == "hotspot_ssid":
                    item.value = self._hotspot_ssid
                elif item.key == "hotspot_password":
                    item.value = self._hotspot_password

        # Clear option text render cache so dynamic labels re-render instantly
        if hasattr(self.app, "_option_cache"):
            self.app._option_cache.clear()

        # Rebuild indexes and refresh UI
        self.app._rebuild_indexes()
        self.app._refresh_all_ui()

    # =========================================================================
    #  ITEM FACTORY & HELPER METHODS
    # =========================================================================

    @staticmethod
    def _make_item(**kwargs) -> ConfigItem:
        item = ConfigItem(**kwargs)
        item.exists_in_target = True
        item.initial_value = item.value
        item._initial_loaded = True
        return item

    def _find_script(self, name: str) -> str:
        p = shutil.which(name)
        if p:
            return p
        candidates = [
            Path("/mnt/zram1/network") / name,
            Path("/mnt/zram1/omarchy-quattro/shell/plugins/panels/network") / name,
            Path("/mnt/zram1/omarchy-quattro/bin") / name,
        ]
        for c in candidates:
            if c.exists():
                return str(c)
        return name

    @staticmethod
    def _get_exec_env() -> dict[str, str]:
        env = os.environ.copy()
        extra_paths = ["/mnt/zram1/omarchy-quattro/bin", "/mnt/zram1/network"]
        current_path = env.get("PATH", "")
        for p in extra_paths:
            if os.path.exists(p) and p not in current_path:
                current_path = f"{p}:{current_path}"
        env["PATH"] = current_path
        return env

    def _run_cmd(self, args: list[str], timeout: int = 5) -> str:
        try:
            res = subprocess.run(
                args, capture_output=True, text=True, stdin=subprocess.DEVNULL,
                env=self._get_exec_env(), timeout=timeout
            )
            return res.stdout
        except (subprocess.TimeoutExpired, Exception):
            return ""

    def _get_wifi_device(self) -> str:
        for line in self._run_cmd(["nmcli", "-t", "-f", "DEVICE,TYPE", "device", "status"]).splitlines():
            parts = _split_nmcli_line(line)
            if len(parts) >= 2 and parts[1] == "wifi":
                return parts[0]
        return ""

    def _get_active_wifi_connection(self) -> dict[str, Any] | None:
        for line in self._run_cmd(["nmcli", "-t", "-f", "NAME,UUID,TYPE,DEVICE", "connection", "show", "--active"]).splitlines():
            if not line:
                continue
            parts = _split_nmcli_line(line)
            if len(parts) >= 4 and parts[2] == "802-11-wireless":
                uuid = parts[1]
                mode_out = self._run_cmd(["nmcli", "-t", "-f", "802-11-wireless.mode", "connection", "show", uuid])
                mode = "ap" if "mode:ap" in mode_out.replace(" ", "") else "infra"
                return {"ssid": parts[0], "uuid": uuid, "device": parts[3], "mode": mode}
        return None

    def _get_saved_wifi(self) -> list[dict[str, Any]]:
        conns = []
        for line in self._run_cmd(["nmcli", "-t", "-f", "NAME,UUID,TYPE,AUTOCONNECT", "connection", "show"]).splitlines():
            if not line:
                continue
            parts = _split_nmcli_line(line)
            if len(parts) >= 4 and parts[2] == "802-11-wireless":
                conns.append({"name": parts[0], "uuid": parts[1], "autoconnect": parts[3] == "yes"})
        return conns

    def _get_scanned_wifi(self) -> list[dict[str, Any]]:
        scans = []
        seen: set[str] = set()
        for line in self._run_cmd(["nmcli", "-t", "-f", "IN-USE,SSID,SECURITY,SIGNAL", "device", "wifi", "list"]).splitlines():
            if not line:
                continue
            parts = _split_nmcli_line(line)
            if len(parts) < 4:
                continue
            in_use = parts[0].strip() == "*"
            ssid = parts[1]
            if not ssid or ssid in seen:
                continue
            seen.add(ssid)
            security = parts[2] if parts[2] else "Open"
            if security == "--":
                security = "Open"
            try:
                signal = int(parts[3])
            except ValueError:
                signal = 0
            scans.append({"in_use": in_use, "ssid": ssid, "security": security, "signal": signal})
        return scans

    def _get_hotspot_clients(self, wifi_dev: str | None) -> int:
        if not wifi_dev:
            return 0
        try:
            res = subprocess.run(
                ["sudo", "-n", "iw", "dev", wifi_dev, "station", "dump"],
                capture_output=True, text=True, stdin=subprocess.DEVNULL, timeout=5
            )
            if res.returncode != 0:
                res = subprocess.run(
                    ["iw", "dev", wifi_dev, "station", "dump"],
                    capture_output=True, text=True, stdin=subprocess.DEVNULL, timeout=5
                )
            return len(re.findall(r"^Station", res.stdout, re.MULTILINE))
        except Exception:
            return 0

    @staticmethod
    def _is_uuid(s: str) -> bool:
        return bool(re.match(r'^[a-f0-9]{8}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{12}$', s))

    @staticmethod
    def _signal_bar(signal: int) -> str:
        if signal >= 80: return "▂▄▆█"
        if signal >= 60: return "▂▄▆_"
        if signal >= 40: return "▂▄__"
        if signal >= 20: return "▂___"
        return "____"
